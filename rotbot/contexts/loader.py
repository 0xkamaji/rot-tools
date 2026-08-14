import json
import os
from pathlib import Path
import re
import tomllib
from typing import NamedTuple

from rotbot.contexts import documents
from rotbot.contexts.identifiers import (
    ContextIdentifierError,
    legacy_context_id,
    new_context_id,
    validate_context_id
)
from rotbot.contexts.paths import contexts_root
from rotbot.ui.terminal import rot_continue, rot_say, rot_table


CONTEXT_ROOT = contexts_root()
PROJECT_CONTEXT_CATEGORY = "projects"
CONTEXT_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class ContextError(Exception):
    pass


class Context(NamedTuple):
    name: str
    identity: str
    state: str
    id: str | None = None
    learned: str = ""
    knowledge: tuple = ()
    relationships: tuple = ()


class ProjectDocument(NamedTuple):
    filename: str
    disclosure: str
    content: str


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


def _existing_project_document(directory, filename):
    path = directory / filename
    if path.is_symlink():
        raise ContextError(
            f"Invalid {filename.removesuffix('.md')} document for context: {directory.name}"
        )
    return path


def context_paths(name):
    root = CONTEXT_ROOT.resolve()
    category = CONTEXT_ROOT / PROJECT_CONTEXT_CATEGORY
    directory = project_context_directory(name)
    try:
        documents.recover_interrupted_migration(directory)
        resolved_category = category.resolve(strict=True)
        resolved_directory = directory.resolve(strict=True)
    except documents.ContextDocumentError as error:
        raise ContextError(str(error)) from None
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
    try:
        documents.ensure_structure(resolved_directory, "project")
    except documents.ContextDocumentError as error:
        raise ContextError(str(error)) from None
    identity_path = resolved_directory / "identity.md"
    if not identity_path.is_file():
        raise ContextError(f"Unknown or invalid context: {name}")
    return identity_path, resolved_directory / "private" / "state.md"


def _context_paths(name):
    return context_paths(name)


def list_contexts():
    category = CONTEXT_ROOT / PROJECT_CONTEXT_CATEGORY
    if not category.exists():
        return ()
    try:
        documents.recover_interrupted_migrations(category)
        entries = tuple(category.iterdir())
    except (OSError, documents.ContextDocumentError) as error:
        raise ContextError(f"Could not list contexts: {error}") from None

    names = []
    for entry in entries:
        try:
            _context_paths(entry.name)
        except ContextError:
            continue
        names.append(entry.name)
    return tuple(sorted(names))


def load_context(name, *, view="full"):
    identity_path, _state_path = context_paths(name)
    directory = identity_path.parent
    metadata_path = directory / "metadata.toml"
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
        semantic_paths = documents.semantic_files(directory, view)
        knowledge = tuple(
            ProjectDocument(
                path.name,
                "identity" if path.parent == directory else path.parent.name,
                path.read_text(encoding="utf-8")
            )
            for path in semantic_paths
        )
        state = next(
            (item.content for item in reversed(knowledge) if item.filename == "state.md"),
            ""
        )
        learned = next(
            (item.content for item in reversed(knowledge) if item.filename == "learned.md"),
            ""
        )
        return Context(
            name=name,
            identity=(directory / "identity.md").read_text(encoding="utf-8"),
            state=state,
            id=context_id,
            learned=learned,
            knowledge=knowledge,
            relationships=documents.load_relationships(directory)
        )
    except (
        OSError, UnicodeError, tomllib.TOMLDecodeError, ContextIdentifierError,
        documents.ContextDocumentError
    ) as error:
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


def build_context_prompt(name, *, view="full"):
    context = load_context(name, view=view)
    label = context.name.upper()
    return (
        f"{label} CONTEXT IDENTITY (READ-ONLY)\n"
        "--------------------------------------\n"
        f"{context.identity}\n\n"
        f"{label} CONTEXT KNOWLEDGE (READ-ONLY)\n"
        "---------------------------------------\n"
        + "\n\n".join(
            f"[{item.disclosure}/{item.filename}]\n{item.content}"
            for item in context.knowledge if item.filename != "identity.md"
        )
    )


def atomic_replace_state(name, content):
    _identity_path, state_path = context_paths(name)
    directory = state_path.parent.parent
    private = directory / "private"
    private.mkdir(mode=0o700, exist_ok=True)
    state_path = private / "state.md"
    temporary_path = state_path.with_suffix(".tmp")
    temporary_path.write_text(content.rstrip() + "\n", encoding="utf-8")
    if os.name != "nt":
        os.chmod(temporary_path, 0o600)
    temporary_path.replace(state_path)


def context_list(args):
    from rotbot.contexts.entities import (
        EntityContextError, list_assistant_contexts, list_user_contexts
    )
    from rotbot.contexts.machines import MachineContextError, list_machine_contexts
    from rotbot.contexts.people import PersonContextError, list_person_contexts

    try:
        names = list_contexts()
        person_contexts = tuple(
            person for person in list_person_contexts()
            if person.role == "contact"
        )
        users = list_user_contexts()
        assistants = list_assistant_contexts()
        machine_contexts = list_machine_contexts()
    except (
        ContextError, MachineContextError, PersonContextError,
        EntityContextError
    ) as error:
        rot_say(str(error))
        return 1

    rot_say("CONTEXTS")
    rows = (
        tuple(("project", name) for name in names)
        + tuple(("user", item.name) for item in users)
        + tuple(("assistant", item.name) for item in assistants)
        + tuple(("contact", person.name) for person in person_contexts)
        + tuple(("machine", machine.name) for machine in machine_contexts)
    )
    if rows:
        rot_table(("TYPE", "NAME"), rows, fill=False)
    else:
        rot_continue("(none)")
    return 0


def _available_context_entries():
    from rotbot.contexts.entities import list_assistant_contexts, list_user_contexts
    from rotbot.contexts.machines import list_machine_contexts
    from rotbot.contexts.people import list_person_contexts

    return (
        tuple(("project", name) for name in list_contexts())
        + tuple(("user", item.name) for item in list_user_contexts())
        + tuple(("assistant", item.name) for item in list_assistant_contexts())
        + tuple(
            ("contact", person.name)
            for person in list_person_contexts() if person.role == "contact"
        )
        + tuple(("machine", machine.name) for machine in list_machine_contexts())
    )


def _show_current_context(inspected=None):
    from rotbot.contexts.inspection import (
        ContextInspectionError,
        inspect_current_context,
        render_inspected_context
    )

    try:
        if inspected is None:
            inspected = inspect_current_context(bootstrap=False)
    except ContextInspectionError as error:
        rot_say(str(error))
        return 2
    rot_say(render_inspected_context(inspected))
    return 1 if inspected.warnings else 0


def _show_project_context(name):
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
        "KNOWLEDGE (general/ + private/; read-only)\n"
        "------------------------------------------\n"
        + ("\n\n".join(
            f"{item.disclosure}/{item.filename}\n{item.content.rstrip()}"
            for item in context.knowledge if item.filename != "identity.md"
        ) or "(none)")
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


def _show_entity_context(name, context_type):
    from rotbot.contexts import entities

    try:
        entity, documents = (
            entities.load_user_documents(name)
            if context_type == "user"
            else entities.load_assistant_documents(name)
        )
    except entities.EntityContextError as error:
        rot_say(str(error))
        return 1
    metadata = entities.render_metadata(entity).rstrip()
    blocks = [f"METADATA (metadata.toml; read-only)\n"
              f"------------------------------------\n{metadata}"]
    for document in documents:
        populated = [
            f"## {heading}\n\n{content}" if heading is not None else content
            for heading, content in document.sections
        ]
        if populated:
            title = f"{document.filename.removesuffix('.md').upper()} " \
                    f"({document.filename}; read-only)"
            blocks.append(f"{title}\n{'-' * len(title)}\n" + "\n\n".join(populated))
    if context_type == "assistant":
        path = entities.entity_directory(entity) / "capabilities.toml"
        if path.is_file() and not path.is_symlink():
            content = path.read_text(encoding="utf-8").rstrip()
            blocks.append(
                "CAPABILITIES (capabilities.toml; policy, not enforcement)\n"
                "---------------------------------------------------------\n"
                + content
            )
    rot_say(f"{context_type.upper()} CONTEXT: {entity.name} ({entity.display_name})")
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
    from rotbot.contexts.entities import EntityContextError
    from rotbot.contexts.machines import MachineContextError
    from rotbot.contexts.people import PersonContextError

    target = getattr(args, "target", None)
    name = getattr(args, "name", None)

    # Bare show renders the active session context.
    if not target and name is None:
        return _show_current_context(getattr(args, "inspected_context", None))

    # Target-specific knowledge show using learning module's resolution.
    if target in ("user", "assistant", "project", "machine"):
        from rotbot.contexts import learning
        try:
            context, directory = learning._resolve(
                target, getattr(args, "inspected_context", None)
            )
        except learning.LearningError as error:
            rot_say(str(error))
            return 1

        selected = learning._select_document(directory, allow_new=False)
        if selected is learning.EXIT:
            rot_say("Show cancelled. No files were changed.")
            return 0
        if selected is learning.BACK:
            rot_say("Show cancelled. No files were changed.")
            return 0

        rot_say(learning._show_document(selected))
        return 0

    if target == "contact":
        if not name:
            rot_say("Contact name is required for 'context show contact'.")
            return 1
        from rotbot.contexts import learning
        try:
            context, directory = learning._resolve(
                "contact", inspected=None, reference=name
            )
        except learning.LearningError as error:
            rot_say(str(error))
            return 1

        selected = learning._select_document(directory, allow_new=False)
        if selected is learning.EXIT:
            rot_say("Show cancelled. No files were changed.")
            return 0
        if selected is learning.BACK:
            rot_say("Show cancelled. No files were changed.")
            return 0

        rot_say(learning._show_document(selected))
        return 0

    # Named full-context display for an explicit saved context reference.
    reference = target or name
    try:
        entries = _available_context_entries()
    except (
        ContextError, MachineContextError, PersonContextError,
        EntityContextError
    ) as error:
        rot_say(str(error))
        return 1
    try:
        validate_context_name(reference)
    except ContextError as error:
        rot_say(str(error))
        return 1
    matches = tuple(entry for entry in entries if entry[1] == reference)
    if len(matches) > 1:
        context_types = ", ".join(context_type for context_type, _name in matches)
        rot_say(
            f"Context name '{reference}' is ambiguous; multiple context "
            f"types exist: {context_types}.\n\n"
            "Use a unique context name."
        )
        return 1
    if not matches:
        rot_say(f"Unknown or invalid context: {reference}")
        return 1
    context_type, name = matches[0]
    if context_type in {"user", "assistant"}:
        return _show_entity_context(name, context_type)
    if context_type == "contact":
        return _show_person_context(name)
    if context_type == "machine":
        return _show_machine_context(name)
    return _show_project_context(name)
