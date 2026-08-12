import json
from pathlib import Path
import re
import tomllib
from typing import NamedTuple

from rotbot.contexts.identifiers import (
    ContextIdentifierError,
    legacy_context_id,
    new_context_id,
    validate_context_id
)
from rotbot.ui.terminal import rot_continue, rot_say, rot_table


CONTEXT_ROOT = Path(__file__).resolve().parents[2] / "context"
PROJECT_CONTEXT_CATEGORY = "projects"
CONTEXT_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class ContextError(Exception):
    pass


class Context(NamedTuple):
    name: str
    identity: str
    state: str
    id: str | None = None


def validate_context_name(name):
    if not isinstance(name, str) or not CONTEXT_NAME_PATTERN.fullmatch(name):
        raise ContextError(f"Invalid context name: {name}")
    return name


def render_project_metadata(name, context_id=None):
    validate_context_name(name)
    try:
        context_id = validate_context_id(context_id or new_context_id())
    except ContextIdentifierError as error:
        raise ContextError(str(error)) from None
    return (
        'type = "project"\n'
        f"id = {json.dumps(context_id)}\n"
        f"name = {json.dumps(name)}\n"
    )


def project_context_directory(name):
    validate_context_name(name)
    return CONTEXT_ROOT / PROJECT_CONTEXT_CATEGORY / name


def context_paths(name):
    root = CONTEXT_ROOT.resolve()
    category = CONTEXT_ROOT / PROJECT_CONTEXT_CATEGORY
    directory = project_context_directory(name)
    try:
        resolved_category = category.resolve(strict=True)
        resolved_directory = directory.resolve(strict=True)
    except OSError:
        raise ContextError(f"Unknown or invalid context: {name}") from None

    if (
        category.is_symlink()
        or not resolved_category.is_dir()
        or resolved_category.parent != root
        or directory.is_symlink()
        or not resolved_directory.is_dir()
        or resolved_directory.parent != resolved_category
    ):
        raise ContextError(f"Unknown or invalid context: {name}")

    identity_path = resolved_directory / "identity.md"
    state_path = resolved_directory / "state.md"
    if (
        identity_path.is_symlink()
        or state_path.is_symlink()
        or not identity_path.is_file()
        or not state_path.is_file()
    ):
        raise ContextError(f"Unknown or invalid context: {name}")

    return identity_path, state_path


def _context_paths(name):
    return context_paths(name)


def list_contexts():
    try:
        entries = tuple((CONTEXT_ROOT / PROJECT_CONTEXT_CATEGORY).iterdir())
    except OSError as error:
        raise ContextError(f"Could not list contexts: {error}") from None

    names = []
    for entry in entries:
        try:
            _context_paths(entry.name)
        except ContextError:
            continue
        names.append(entry.name)
    return tuple(sorted(names))


def load_context(name):
    identity_path, state_path = context_paths(name)
    metadata_path = identity_path.parent / "metadata.toml"
    try:
        if metadata_path.exists():
            if metadata_path.is_symlink() or not metadata_path.is_file():
                raise ContextError(f"Invalid project metadata: {name}")
            metadata = tomllib.loads(metadata_path.read_text(encoding="utf-8"))
            if metadata.get("type") != "project" or metadata.get("name") != name:
                raise ContextError(f"Invalid project metadata: {name}")
            context_id = validate_context_id(metadata.get("id"))
        else:
            context_id = legacy_context_id("project", name)
        return Context(
            name=name,
            identity=identity_path.read_text(encoding="utf-8"),
            state=state_path.read_text(encoding="utf-8"),
            id=context_id
        )
    except (OSError, UnicodeError, tomllib.TOMLDecodeError, ContextIdentifierError) as error:
        raise ContextError(f"Could not load context '{name}': {error}") from None


def load_context_reference(reference):
    matches = []
    for name in list_contexts():
        context = load_context(name)
        if reference in {context.id, context.name}:
            matches.append(context)
    if not matches:
        raise ContextError(f"Unknown project context reference: {reference}")
    if len(matches) > 1:
        raise ContextError(f"Ambiguous project context reference: {reference}")
    return matches[0]


def load_vision(name):
    identity_path, _state_path = context_paths(name)
    vision_path = identity_path.parent / "vision.md"
    if vision_path.is_symlink():
        raise ContextError(f"Invalid vision document for context: {name}")
    if not vision_path.exists():
        return None
    if not vision_path.is_file():
        raise ContextError(f"Invalid vision document for context: {name}")

    try:
        return vision_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise ContextError(f"Could not load vision for context '{name}': {error}") from None


def build_context_prompt(name):
    context = load_context(name)
    label = context.name.upper()
    return (
        f"{label} CONTEXT IDENTITY (READ-ONLY)\n"
        "--------------------------------------\n"
        f"{context.identity}\n\n"
        f"{label} CONTEXT STATE (READ-ONLY)\n"
        "-----------------------------------\n"
        f"{context.state}"
    )


def atomic_replace_state(name, content):
    _identity_path, state_path = context_paths(name)
    temporary_path = state_path.with_suffix(".tmp")
    temporary_path.write_text(content.rstrip() + "\n", encoding="utf-8")
    temporary_path.replace(state_path)


def context_list(args):
    from rotbot.contexts.machines import MachineContextError, list_machine_contexts
    from rotbot.contexts.people import PersonContextError, list_person_contexts

    try:
        names = list_contexts()
        person_contexts = list_person_contexts()
        machine_contexts = list_machine_contexts()
    except (ContextError, MachineContextError, PersonContextError) as error:
        rot_say(str(error))
        return 1

    rot_say("CONTEXTS")
    rows = (
        tuple(("project", name) for name in names)
        + tuple(("person", person.name) for person in person_contexts)
        + tuple(("machine", machine.name) for machine in machine_contexts)
    )
    if rows:
        rot_table(("TYPE", "NAME"), rows, fill=False)
    else:
        rot_continue("(none)")
    return 0


def _available_context_entries():
    from rotbot.contexts.machines import list_machine_contexts
    from rotbot.contexts.people import list_person_contexts

    return (
        tuple(("project", name) for name in list_contexts())
        + tuple(("person", person.name) for person in list_person_contexts())
        + tuple(("machine", machine.name) for machine in list_machine_contexts())
    )


def _choose_context_to_show(entries):
    exit_number = len(entries) + 1
    rot_say(
        "Which context would you like to show?\n\n"
        + "\n".join(
            f"  {index}. {context_type}: {name}"
            for index, (context_type, name) in enumerate(entries, 1)
        )
        + f"\n  {exit_number}. Exit"
    )
    while True:
        try:
            answer = input("> ").strip()
        except EOFError:
            return None
        if answer.lower() in {"", "exit", "e", "quit", "q"}:
            return None
        if answer == str(exit_number):
            return None
        if answer.isdigit() and 1 <= int(answer) <= len(entries):
            return entries[int(answer) - 1]
        rot_say(f"Please choose a number from 1 to {exit_number}.")


def _choose_context_scope():
    rot_say(
        "Which context would you like to show?\n\n"
        "  1. Current session - identities, machine, directory, and project\n"
        "  2. Existing contexts - choose a saved project, person, or machine\n"
        "  3. Exit"
    )
    while True:
        try:
            answer = input("> ").strip().lower()
        except EOFError:
            return None
        if answer in {"", "exit", "e", "quit", "q", "3"}:
            return None
        if answer in {"1", "current", "session", "current session"}:
            return "current"
        if answer in {"2", "existing", "saved", "other"}:
            return "existing"
        rot_say("Please choose 1, 2, or 3.")


def _show_current_context(vision_only):
    if vision_only:
        rot_say("--vision is only supported when showing a saved project context.")
        return 1
    from rotbot.contexts.inspection import (
        ContextInspectionError,
        inspect_current_context,
        render_inspected_context
    )

    try:
        inspected = inspect_current_context(bootstrap=False)
    except ContextInspectionError as error:
        rot_say(str(error))
        return 2
    rot_say(render_inspected_context(inspected))
    return 1 if inspected.warnings else 0


def _show_project_context(name, vision_only):
    if vision_only:
        try:
            vision = load_vision(name)
        except ContextError as error:
            rot_say(str(error))
            return 1

        if vision is None:
            rot_say(f"No vision document exists for context '{name}'.")
            return 0

        rot_say(f"VISION: {name}")
        rot_continue(
            "Vision describes possible future direction. It is not current "
            "state, an approved requirement, or authorization to implement "
            "anything.\n\n"
            f"{vision}"
        )
        return 0

    try:
        context = load_context(name)
    except ContextError as error:
        rot_say(str(error))
        return 1

    rot_say(f"CONTEXT: {context.name}")
    rot_continue(
        "IDENTITY (identity.md; read-only)\n"
        "---------------------------------\n"
        f"{context.identity}\n\n"
        "STATE (state.md; read-only)\n"
        "---------------------------\n"
        f"{context.state}"
    )
    return 0


def _show_person_context(name):
    from rotbot.contexts.people import (
        PersonContextError,
        load_person_documents,
        render_person_files
    )

    try:
        person, documents = load_person_documents(name)
    except PersonContextError as error:
        rot_say(str(error))
        return 1
    metadata = render_person_files(person)["metadata.toml"].rstrip()
    metadata_title = "METADATA (metadata.toml; read-only)"
    blocks = [f"{metadata_title}\n{'-' * len(metadata_title)}\n{metadata}"]
    has_recorded_information = False
    for document in documents:
        populated = []
        for heading, content in document.sections:
            populated.append(
                f"## {heading}\n\n{content}" if heading is not None else content
            )
        if not populated:
            continue
        has_recorded_information = True
        label = document.filename.removesuffix(".md").upper()
        title = f"{label} ({document.filename}; read-only)"
        blocks.append(f"{title}\n{'-' * len(title)}\n" + "\n\n".join(populated))
    if not has_recorded_information:
        blocks.append("(no recorded information)")
    rot_say(f"PERSON CONTEXT: {person.name} ({person.display_name})")
    rot_continue("\n\n".join(blocks))
    return 0


def _show_machine_context(name):
    from rotbot.contexts.machines import MachineContextError, load_machine_files

    try:
        machine, documents = load_machine_files(name)
    except MachineContextError as error:
        rot_say(str(error))
        return 1
    blocks = []
    for document in documents:
        label = document.filename.rsplit(".", 1)[0].upper()
        title = f"{label} ({document.filename}; read-only)"
        content = document.content.rstrip() or "(empty)"
        blocks.append(f"{title}\n{'-' * len(title)}\n{content}")
    rot_say(f"MACHINE CONTEXT: {machine.name} ({machine.display_name})")
    rot_continue("\n\n".join(blocks))
    return 0


def context_show(args):
    from rotbot.contexts.machines import MachineContextError
    from rotbot.contexts.people import PersonContextError

    try:
        entries = _available_context_entries()
    except (ContextError, MachineContextError, PersonContextError) as error:
        rot_say(str(error))
        return 1
    if args.name:
        try:
            validate_context_name(args.name)
        except ContextError as error:
            rot_say(str(error))
            return 1
        matches = tuple(entry for entry in entries if entry[1] == args.name)
        if len(matches) > 1:
            context_types = ", ".join(context_type for context_type, _name in matches)
            rot_say(
                f"Context name '{args.name}' is ambiguous; multiple context "
                f"types exist: {context_types}.\n\n"
                "Run 'rot context show' without a name to choose one."
            )
            return 1
        if not matches:
            rot_say(f"Unknown or invalid context: {args.name}")
            return 1
        context_type, name = matches[0]
    else:
        scope = _choose_context_scope()
        if scope is None:
            rot_say("Context display cancelled.")
            return 0
        if scope == "current":
            return _show_current_context(getattr(args, "vision", False))
        if not entries:
            rot_say("No saved contexts are available to show.")
            return 1
        selected = _choose_context_to_show(entries)
        if selected is None:
            rot_say("Context display cancelled.")
            return 0
        context_type, name = selected

    vision_only = getattr(args, "vision", False)
    if context_type != "project" and vision_only:
        rot_say("--vision is only supported for project contexts.")
        return 1
    if context_type == "person":
        return _show_person_context(name)
    if context_type == "machine":
        return _show_machine_context(name)
    return _show_project_context(name, vision_only)
