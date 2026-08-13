import json
import os
from pathlib import Path
import re
import stat
import tempfile
import shutil
from types import SimpleNamespace

from rotbot.agents.invocation import AIRequest, invoke
from rotbot.commands.machine import inspect_local_machine, show_inspection
from rotbot.contexts import entities, loader, machines, people
from rotbot.contexts.matching import (
    MatchError,
    build_source_match_toml,
    inspect_source_project,
    load_match_definition,
    match_source_definition,
    parse_match_toml
)
from rotbot.contexts.identifiers import new_context_id
from rotbot.contexts.config import (
    ConfigError,
    config_path,
    get_context_binding,
    load_config,
    remove_context_bindings,
    set_context_binding
)
from rotbot.ui.terminal import rot_continue, rot_say


IGNORED_NAMES = {
    ".git", ".hg", ".svn", ".cache", ".mypy_cache", ".pytest_cache",
    ".tox", ".venv", "venv", "env", "__pycache__", "node_modules",
    "vendor", "dist", "build", "target", "coverage", ".next"
}
SENSITIVE_PARTS = (
    "credential", "secret", "token", "private_key", "id_rsa", "id_ed25519"
)
BINARY_SUFFIXES = {
    ".7z", ".a", ".bin", ".class", ".dll", ".dylib", ".exe", ".gif",
    ".gz", ".ico", ".jar", ".jpeg", ".jpg", ".o", ".pdf", ".png",
    ".pyc", ".so", ".tar", ".webp", ".woff", ".woff2", ".zip"
}
PATH_PRIORITY = (
    "pyproject.toml", "package.json", "Cargo.toml", "go.mod", "composer.json",
    "Gemfile", "setup.py", "main.py", "app.py", "cli.py", "parser.py",
    "contexts.py", "index.html", "src/", "app/", "lib/", "context/",
    "tests/", "assets/", "scripts/", "README.md", "README"
)
EXCERPT_NAMES = {
    "readme", "readme.md", "readme.rst", "readme.txt", "pyproject.toml",
    "package.json", "cargo.toml", "go.mod", "composer.json", "gemfile",
    "setup.py", "main.py", "app.py", "cli.py", "parser.py", "contexts.py",
    "dockerfile", "makefile"
}
MAX_TREE_ENTRIES = 100
MAX_EXCERPT_FILES = 8
MAX_EXCERPT_BYTES = 12_000
MAX_SYNOPSIS_BYTES = 64_000
MAX_AGENT_OUTPUT_BYTES = 250_000
MAX_REMOTES = 32
INITIAL_VISION = (
    "# Vision\n\n"
    "<!-- Future ideas and vision for the growth of this project belong here. "
    "Add each idea as a bullet point beginning with '-'. -->\n"
)
DOCUMENT_NOTES = {
    "identity": (
        "Stable facts about what this project is, its purpose, audience, and "
        "architecture. Add each fact as a bullet point beginning with '-'."
    ),
    "state": (
        "Current facts about what exists in this project. Add each fact as a "
        "bullet point beginning with '-'."
    )
}
PLACEHOLDER_POINT = "Context created by RotBot. AI enrichment has not yet been completed."
SENSITIVE_CONTENT_PATTERN = re.compile(
    r"(?i)(?:api[_-]?key|access[_-]?token|auth[_-]?token|password|passwd|"
    r"private[_-]?key|client[_-]?secret)\s*[:=]|"
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----|https?://[^\s/:]+:[^\s/@]+@"
)
ABSOLUTE_PATH_PATTERN = re.compile(
    r"(?m)(?:^|[\s`'(\[])"
    r"(?:/(?:[^\s/]+/)+[^\s]*|[A-Za-z]:\\[^\s]+)"
)


class ContextCreationError(Exception):
    pass


def _is_sensitive(name):
    lowered = name.lower()
    return (
        lowered == ".env"
        or lowered.startswith(".env.")
        or Path(lowered).suffix in {".key", ".pem", ".p12", ".pfx"}
        or any(part in lowered for part in SENSITIVE_PARTS)
    )


def _safe_entries(project):
    try:
        entries = tuple(sorted(project.iterdir(), key=lambda item: item.name.lower()))
    except OSError as error:
        raise ContextCreationError(f"Could not inspect project directory:\n{error}") from None

    safe = []
    for entry in entries:
        if len(safe) >= MAX_TREE_ENTRIES:
            break
        if (
            entry.name in IGNORED_NAMES
            or entry.name.startswith(".")
            or _is_sensitive(entry.name)
            or entry.suffix.lower() in BINARY_SUFFIXES
            or entry.is_symlink()
        ):
            continue
        try:
            if entry.is_dir():
                safe.append((entry, f"{entry.name}/"))
            elif entry.is_file():
                safe.append((entry, entry.name))
        except OSError:
            continue
    return tuple(safe)


def _match_paths(entries):
    labels = [label for _entry, label in entries]
    priority = {name: index for index, name in enumerate(PATH_PRIORITY)}
    selected = sorted(
        labels,
        key=lambda label: (priority.get(label, len(priority)), label.lower())
    )[:5]
    if not selected:
        raise ContextCreationError(
            "No stable top-level project paths were available for deterministic matching."
        )
    optional = tuple(label for label in labels if label not in selected)[:5]
    return tuple(selected), optional


def _read_excerpt(path):
    descriptor = None
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        )
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            return None
        with os.fdopen(descriptor, "rb") as source:
            descriptor = None
            content = source.read(MAX_EXCERPT_BYTES + 1)
    except OSError:
        return None
    finally:
        if descriptor is not None:
            os.close(descriptor)
    if b"\0" in content[:4096]:
        return None
    try:
        text = content[:MAX_EXCERPT_BYTES].decode("utf-8")
    except UnicodeError:
        return None
    if SENSITIVE_CONTENT_PATTERN.search(text):
        return None
    if len(content) > MAX_EXCERPT_BYTES:
        text += "\n[truncated]"
    return text


def _inspect_project(project, remotes):
    if len(remotes) > MAX_REMOTES:
        raise ContextCreationError("Project has too many configured Git remotes.")
    entries = _safe_entries(project)
    required_paths, optional_paths = _match_paths(entries)
    excerpts = []
    total_size = 0
    for entry, label in entries:
        if len(excerpts) >= MAX_EXCERPT_FILES or entry.is_dir():
            continue
        if entry.name.lower() not in EXCERPT_NAMES:
            continue
        excerpt = _read_excerpt(entry)
        if excerpt is None:
            continue
        rendered = f"--- {label} ---\n{excerpt}"
        encoded_size = len(rendered.encode("utf-8"))
        if total_size + encoded_size > MAX_SYNOPSIS_BYTES:
            break
        excerpts.append(rendered)
        total_size += encoded_size

    synopsis = (
        f"Project directory name: {project.name}\n"
        "Normalized Git remotes:\n"
        + ("\n".join(f"- {remote}" for remote in remotes) or "- none")
        + "\n\nTop-level tree:\n"
        + "\n".join(f"- {label}" for _entry, label in entries)
        + "\n\nStable identifying paths:\n"
        + "\n".join(f"- {path}" for path in required_paths)
    )
    if len(synopsis.encode("utf-8")) > MAX_SYNOPSIS_BYTES:
        raise ContextCreationError("Project synopsis exceeds the safe size limit.")
    if excerpts:
        heading = "\n\nSelected bounded excerpts:\n\n"
        for excerpt in excerpts:
            addition = heading + excerpt
            if len((synopsis + addition).encode("utf-8")) > MAX_SYNOPSIS_BYTES:
                break
            synopsis += addition
            heading = "\n\n"
    return synopsis, required_paths, optional_paths


def _agent_prompt(name, synopsis):
    return (
        f"Draft context documents for the project named {name}. Use only the "
        "bounded synopsis below. Do not inspect or modify any files. Return only "
        "a JSON object with exactly two string keys: `identity` and `state`. "
        "Do not use a code fence or add commentary.\n\n"
        "Each document must contain exactly one level-one heading followed by "
        "bullet points beginning with `- `. Do not include section headings, "
        "labels on separate lines, or Markdown comments; RotBot adds its own "
        "document guidance comment.\n\n"
        "The identity Markdown must begin with a level-one heading and describe "
        "stable facts: what the project is, its core purpose, intended role or "
        "audience, stable architecture, and repository identity when useful. "
        "Avoid marketing, invented history, temporary status, speculative plans, "
        "future vision, secrets, credentials, and machine-local absolute paths.\n\n"
        "The state Markdown must begin with a level-one heading and describe only "
        "what currently exists: major capabilities, structure, entry points, "
        "implemented integrations, commands, and directly evident limitations. "
        "Avoid roadmaps, speculation, secrets, credentials, and machine-local "
        "absolute paths. Draft only identity.md and state.md; do not draft "
        "match.toml or vision.md.\n\n"
        f"PROJECT SYNOPSIS\n----------------\n{synopsis}"
    )


def _placeholder_documents(name, definition):
    source = definition.source
    git_state = "Git-backed" if source.is_git_repo else "not Git-backed"
    paths = ", ".join(f"`{path}`" for path in source.required_paths)
    return {
        "identity": (
            f"# {name}\n\n<!-- {DOCUMENT_NOTES['identity']} -->\n\n"
            f"- {PLACEHOLDER_POINT}\n"
            f"- This is the `{name}` project context.\n"
        ),
        "state": (
            f"# {name} State\n\n<!-- {DOCUMENT_NOTES['state']} -->\n\n"
            f"- {PLACEHOLDER_POINT}\n"
            f"- The project is {git_state}.\n"
            f"- Portable recognition currently requires: {paths}.\n"
        )
    }


def _parse_agent_draft(output, project):
    if len(output.encode("utf-8")) > MAX_AGENT_OUTPUT_BYTES:
        raise ContextCreationError("The AI agent returned oversized context documents.")
    try:
        draft = json.loads(output.strip())
    except (TypeError, json.JSONDecodeError) as error:
        raise ContextCreationError(f"The AI agent returned invalid context JSON: {error}") from None
    if not isinstance(draft, dict) or set(draft) != {"identity", "state"}:
        raise ContextCreationError(
            "The AI agent must return exactly identity and state documents."
        )

    documents = {}
    local_path = str(project)
    for name in ("identity", "state"):
        content = draft[name]
        if not isinstance(content, str):
            raise ContextCreationError(f"Generated {name}.md must be text.")
        content = content.strip()
        if (
            not content
            or not content.startswith("# ")
            or "\0" in content
            or len(content.encode("utf-8")) > 100_000
            or local_path in content
            or ABSOLUTE_PATH_PATTERN.search(content)
            or any(
                ord(character) < 32 and character not in {"\n", "\t"}
                for character in content
            )
        ):
            raise ContextCreationError(f"Generated {name}.md is empty or invalid.")
        lines = content.splitlines()
        heading = lines[0]
        points = []
        paragraph = []
        section = None
        in_comment = False

        def add_point(point):
            point = point.strip()
            if point:
                points.append(f"{section}: {point}" if section else point)

        def finish_paragraph():
            if paragraph:
                add_point(" ".join(paragraph))
                paragraph.clear()

        for line in lines[1:]:
            stripped = line.strip()
            if in_comment:
                if "-->" in stripped:
                    in_comment = False
                continue
            if stripped.startswith("<!--"):
                in_comment = "-->" not in stripped
                continue
            if not stripped:
                finish_paragraph()
                continue
            if stripped.startswith("#"):
                finish_paragraph()
                section = stripped.lstrip("#").strip().rstrip(":") or None
                continue
            if stripped.endswith(":") and not stripped.startswith("-"):
                finish_paragraph()
                section = stripped[:-1].strip() or None
                continue
            if stripped.startswith("- "):
                finish_paragraph()
                add_point(stripped[2:])
            else:
                paragraph.append(stripped)
        finish_paragraph()
        if not points:
            raise ContextCreationError(f"Generated {name}.md contains no context facts.")
        documents[name] = (
            f"{heading}\n\n<!-- {DOCUMENT_NOTES[name]} -->\n\n"
            + "\n".join(f"- {point}" for point in points)
            + "\n"
        )
    return documents


def _confirm(message, default=False):
    rot_say(f"{message} {'[Y/n]' if default else '[y/N]'}")
    try:
        answer = input("> ").strip().lower()
    except EOFError:
        answer = ""
    if not answer:
        return default
    return answer in {"y", "yes"}


def _read_input():
    try:
        return input("> ").strip()
    except EOFError:
        return None


def _ask_choice(message, choices, default):
    while True:
        rot_say(message)
        answer = _read_input()
        if answer is None:
            return None
        answer = answer.lower()
        if answer in {"exit", "e", "quit", "q"}:
            return None
        if not answer:
            return default
        for value, accepted in choices.items():
            if answer in accepted:
                return value
        rot_say("Please choose one of the listed options.")


def _ask_value(label, default=None):
    while True:
        suffix = f" [{default}]" if default is not None else ""
        rot_say(f"{label}{suffix}:")
        answer = _read_input()
        if answer is None:
            return None
        if answer:
            return answer
        if default is not None:
            return default
        rot_say("A value is required.")


def _choose_related_projects():
    try:
        projects = loader.list_contexts()
    except loader.ContextError as error:
        rot_say(str(error))
        return None
    if not projects:
        rot_say("No existing project contexts are available to relate to this person.")
        return ()
    none_number = len(projects) + 1
    exit_number = len(projects) + 2
    prompt = (
        "Are they related to any existing projects?\n\n"
        + "\n".join(
            f"  {index}. {project}" for index, project in enumerate(projects, 1)
        )
        + f"\n  {none_number}. None"
        + f"\n  {exit_number}. Exit\n\n"
        "Choose one or more numbers separated by commas "
        f"[{none_number}]:"
    )
    while True:
        rot_say(prompt)
        answer = _read_input()
        if answer is None:
            return None
        lowered = answer.lower()
        if lowered in {"", "none", "n", "no", str(none_number)}:
            return ()
        if lowered in {"exit", "e", "quit", "q", str(exit_number)}:
            return None
        choices = [part.strip() for part in answer.split(",")]
        if choices and all(
            choice.isdigit() and 1 <= int(choice) <= len(projects)
            for choice in choices
        ):
            selected = []
            for choice in choices:
                project = projects[int(choice) - 1]
                if project not in selected:
                    selected.append(project)
            return tuple(selected)
        rot_say(
            "Choose project numbers separated by commas, None, or Exit."
        )


def _add_person_context(name, role, display_name, related_projects=()):
    if role in {"user", "assistant"}:
        try:
            entity = (
                entities.build_user_context(name, display_name, related_projects)
                if role == "user"
                else entities.build_assistant_context(name, display_name, related_projects)
            )
            files = entities.render_entity_files(entity)
        except entities.EntityContextError as error:
            rot_say(str(error))
            return 1
        rot_say(f"Create {role} context '{name}' for {display_name}?")
        rot_continue(
            "Proposed files:\n\n"
            + "\n".join(
                f"  context/{entities.CONTEXT_TYPES[entity.context_type]}/"
                f"{name}/{filename}"
                for filename in files
            )
        )
        if not _confirm(f"Create this {role} context?"):
            rot_say("Context creation cancelled. No files were changed.")
            return 0
        try:
            destination = entities.create_entity_context(entity)
        except entities.EntityContextError as error:
            rot_say(str(error))
            return 1
        rot_say(f"{role.title()} context '{name}' created at:\n{destination}")
        return 0
    try:
        person = people.build_person_context(
            name,
            role,
            display_name,
            related_projects
        )
        files = people.render_person_files(person)
    except people.PersonContextError as error:
        rot_say(str(error))
        return 1

    rot_say(f"Create {role} context '{name}' for {display_name}?")
    rot_continue(
        "Proposed files:\n\n"
        + "\n".join(
            f"  {loader.CONTEXT_ROOT / 'contacts' / name / ('metadata.toml' if filename == 'metadata.toml' else 'local/' + filename)}"
            for filename in files
        )
    )
    if not _confirm("Create this person context?"):
        rot_say("Person context creation cancelled. No files were changed.")
        return 0

    try:
        destination = people.create_person_context(
            name,
            role,
            display_name,
            related_projects=related_projects,
            context_id=person.id
        )
    except people.PersonContextError as error:
        rot_say(str(error))
        return 1
    rot_say(f"Person context '{name}' created at:\n{destination}")
    return 0


def _add_machine_context(
    name,
    display_name,
    portable_facts=None,
    local_facts=None,
    create_local=False
):
    try:
        machine = machines.build_machine_context(
            name,
            display_name,
            portable_facts
        )
        files = machines.render_machine_files(machine)
        local_path = (
            machines.local_machine_record_path(name)
            if create_local
            else None
        )
    except machines.MachineContextError as error:
        rot_say(str(error))
        return 1

    rot_say(f"Create machine context '{name}' for {display_name}?")
    rot_continue(
        "Proposed files:\n\n"
        + "\n".join(
            f"  {loader.CONTEXT_ROOT / 'machines' / name / ('metadata.toml' if filename == 'metadata.toml' else 'local/' + filename)}"
            for filename in files
        )
        + (
            f"\n\nApproved local metadata:\n\n  {local_path}"
            if local_path is not None
            else "\n\nNo local machine metadata will be created."
        )
    )
    if not _confirm("Create this machine context?"):
        rot_say("Machine context creation cancelled. No files were changed.")
        return 0

    try:
        destination = machines.create_machine(
            name,
            display_name,
            portable_facts,
            machine.id
        )
    except machines.MachineContextError as error:
        rot_say(str(error))
        return 1
    if create_local:
        try:
            local_destination = machines.create_local_machine_record(
                name,
                local_facts,
                machine.id
            )
        except machines.MachineContextError as error:
            rot_say(
                f"Machine context '{name}' was created at:\n{destination}\n\n"
                f"Local machine metadata could not be created:\n{error}"
            )
            return 1
        rot_say(
            f"Machine context '{name}' created at:\n{destination}\n\n"
            f"Local machine metadata created at:\n{local_destination}"
        )
        return 0
    rot_say(f"Machine context '{name}' created at:\n{destination}")
    return 0


def context_add(args):
    context_type = getattr(args, "context_type", None)
    person_role = context_type if context_type in {"user", "assistant"} else None
    if context_type is None:
        context_type = _ask_choice(
            "Context type:\n\n  1. Project\n  2. Person\n  3. Machine\n"
            "  4. Exit\n\nChoose 1, 2, 3, or 4 [1]:",
            {
                "project": {"1", "project"},
                "person": {"2", "person"},
                "machine": {"3", "machine"},
                None: {"4"}
            },
            "project"
        )
    if context_type is None:
        rot_say("Context creation cancelled. No files were changed.")
        return 0

    if context_type == "project":
        path = _ask_value("Project path", ".")
        if path is None:
            rot_say("Context creation cancelled. No files were changed.")
            return 0
        default_name = Path(path).expanduser().absolute().name or None
        name = _ask_value("Context name", default_name)
        if name is None:
            rot_say("Context creation cancelled. No files were changed.")
            return 0
        project_args = SimpleNamespace(
            name=name,
            path=path,
            agent=getattr(args, "agent", None)
        )
        return _add_project_context(project_args)

    if context_type == "machine":
        name = getattr(args, "name", None)
        if name is None:
            name = _ask_value("Machine context name")
        if name is None:
            rot_say("Context creation cancelled. No files were changed.")
            return 0
        display_default = name.replace("-", " ").replace("_", " ").title()
        display_name = _ask_value("Display name", display_default)
        if display_name is None:
            rot_say("Context creation cancelled. No files were changed.")
            return 0
        try:
            machines.build_machine_context(name, display_name)
        except machines.MachineContextError as error:
            rot_say(str(error))
            return 1
        initialization = _ask_choice(
            "How should this machine context be initialized?\n\n"
            "  1. Inspect this system\n"
            "  2. Leave empty\n"
            "  3. Exit\n\n"
            "Choose 1, 2, or 3 [1]:",
            {
                "inspect": {"1", "inspect", "inspect this system"},
                "empty": {"2", "empty", "leave empty"},
                None: {"3"}
            },
            "inspect"
        )
        if initialization is None:
            rot_say("Context creation cancelled. No files were changed.")
            return 0
        if initialization == "empty":
            return _add_machine_context(name, display_name)

        inspection = inspect_local_machine()
        show_inspection(inspection)
        portable_facts = (
            inspection.portable
            if _confirm("Use the detected portable metadata?", default=True)
            else None
        )
        create_local = False
        if machines.has_local_facts(inspection.local):
            create_local = _confirm(
                "Create local machine metadata with the detected local facts?"
            )
        return _add_machine_context(
            name,
            display_name,
            portable_facts,
            inspection.local if create_local else None,
            create_local
        )

    name = getattr(args, "name", None)
    if name is None:
        name = _ask_value("Person context name")
    if name is None:
        rot_say("Context creation cancelled. No files were changed.")
        return 0
    role = person_role or _ask_choice(
        "Person role:\n\n"
        "  1. Contact - someone known to a RotBot user\n"
        "  2. User - someone who operates RotBot\n"
        "  3. Assistant - an assistant persona such as Rot\n"
        "  4. Exit\n\n"
        "Choose 1, 2, 3, or 4 [1]:",
        {
            "contact": {"1", "contact"},
            "user": {"2", "user"},
            "assistant": {"3", "assistant"},
            None: {"4"}
        },
        "contact"
    )
    if role is None:
        rot_say("Context creation cancelled. No files were changed.")
        return 0
    rot_say(
        "What should this person be called?\n"
        f"Leave blank to use their context name: {name}"
    )
    display_name = _ask_value("Display name", name)
    if display_name is None:
        rot_say("Context creation cancelled. No files were changed.")
        return 0
    related_projects = _choose_related_projects()
    if related_projects is None:
        rot_say("Context creation cancelled. No files were changed.")
        return 0
    return _add_person_context(name, role, display_name, related_projects)


def _destination_exists(destination):
    return os.path.lexists(destination)


def _rollback_context(destination):
    try:
        shutil.rmtree(destination)
        errors = []
    except OSError as error:
        errors = [str(error)]
    return tuple(errors)


def _write_document(path, content):
    with path.open("x", encoding="utf-8") as destination:
        destination.write(content)
        destination.flush()
        os.fsync(destination.fileno())
    if os.name != "nt":
        os.chmod(path, 0o600)


def _atomic_replace_documents(destination, expected, documents):
    originals = {}
    replacements = {}
    try:
        for name in ("identity", "state"):
            path = destination / "local" / f"{name}.md"
            if path.is_symlink() or not path.is_file():
                raise ContextCreationError(f"Invalid project context document: {path.name}")
            original = path.read_text(encoding="utf-8")
            if original != expected[name]:
                raise ContextCreationError(
                    f"Generated enrichment was not applied because {path.name} "
                    "is no longer an unenriched placeholder."
                )
            originals[path] = original
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=path.parent,
                prefix=f".{path.name}.", suffix=".tmp", delete=False
            ) as temporary:
                replacement = Path(temporary.name)
                temporary.write(documents[name])
                temporary.flush()
                os.fsync(temporary.fileno())
            if os.name != "nt":
                os.chmod(replacement, stat.S_IMODE(path.stat().st_mode))
            replacements[path] = replacement
        replaced = []
        try:
            for path, replacement in replacements.items():
                os.replace(replacement, path)
                replaced.append(path)
                replacements[path] = None
        except OSError:
            for path in replaced:
                path.write_text(originals[path], encoding="utf-8")
            raise
    except ContextCreationError:
        raise
    except (OSError, UnicodeError) as error:
        raise ContextCreationError(f"Could not apply AI enrichment: {error}") from None
    finally:
        for replacement in replacements.values():
            if replacement is not None:
                try:
                    replacement.unlink()
                except OSError:
                    pass


def _enrich_project_context(
    name, project, synopsis, destination, agent_name=None,
    parent_command="context develop"
):
    try:
        expected = _placeholder_documents(name, load_match_definition(name))
    except (MatchError, loader.ContextError, ContextCreationError) as error:
        return f"AI enrichment could not start: {error}"
    from rotbot.ui.ai import AIActivityPresenter

    presenter = AIActivityPresenter("developing context", stop_on_stream=False)
    with tempfile.TemporaryDirectory(prefix="rotbot-context-agent-") as agent_directory:
        result = invoke(
            AIRequest(
                purpose="context_development",
                parent_command=parent_command,
                task=_agent_prompt(name, synopsis),
                working_directory=agent_directory,
                agent_name=agent_name,
                timeout=300,
                output_contract="identity/state context documents",
                retries=1,
                isolated=True,
                display_output=False
            ),
            validator=lambda output: _parse_agent_draft(output, project),
            on_event=presenter
        )
    if not result.successful:
        return result.validation_error or f"AI enrichment failed with exit code {result.returncode}."
    try:
        _atomic_replace_documents(destination, expected, result.value)
    except ContextCreationError as error:
        return str(error)
    return None


def _create_and_bind(
    name,
    context_id,
    destination,
    project,
    documents,
    match_document,
    target_config,
    definition
):
    created = False
    binding_created = False
    try:
        destination.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        destination.mkdir(mode=0o700)
        created = True
        _write_document(
            destination / "metadata.toml", loader.render_project_metadata(name, context_id)
        )
        local = destination / "local"
        local.mkdir(mode=0o700)
        (destination / "shareable").mkdir(mode=0o700)
        files = {
            "identity.md": documents["identity"],
            "state.md": documents["state"],
            "vision.md": INITIAL_VISION,
            "match.toml": match_document
        }
        for filename, content in files.items():
            _write_document(local / filename, content)

        current_binding = get_context_binding(name, target_config)
        current_source = current_binding.get("source_path")
        if current_source and Path(current_source).resolve() != project:
            raise ConfigError("The source binding changed during context creation.")
        set_context_binding(name, "source_path", str(project), target_config)
        binding_created = current_source is None
        loaded = loader.load_context(name)
        loaded_match = load_match_definition(name)
        matched = match_source_definition(project, name, loaded_match)
        saved_binding = get_context_binding(name, target_config).get("source_path")
        if (
            not loaded.identity.strip()
            or not loaded.state.strip()
            or loaded_match != definition
            or not matched.strong
            or saved_binding != str(project)
        ):
            raise ContextCreationError("The new project context did not validate after creation.")
    except BaseException as error:
        if binding_created:
            try:
                remove_context_bindings(name, target_config)
            except ConfigError:
                pass
        rollback_errors = _rollback_context(destination) if created else ()
        if isinstance(error, (KeyboardInterrupt, SystemExit)):
            raise
        message = f"Could not create context '{name}':\n{error}"
        if rollback_errors:
            message += "\nRollback could not remove all new artifacts:\n" + "\n".join(
                rollback_errors
            )
        raise ContextCreationError(message) from None


def _add_project_context(args):
    name = args.name
    try:
        loader.validate_context_name(name)
    except loader.ContextError as error:
        rot_say(str(error))
        return 1

    context_root = loader.CONTEXT_ROOT.resolve()
    project_context_root = context_root / loader.PROJECT_CONTEXT_CATEGORY
    destination = loader.project_context_directory(name)
    if _destination_exists(destination):
        rot_say(
            f"Context '{name}' already exists.\n\n"
            f"Use:\n  rot context show {name}"
        )
        return 1

    project = Path(args.path).expanduser()
    if not project.exists() or not project.is_dir():
        rot_say(f"Project path is not a directory:\n{project}")
        return 1
    try:
        project = project.resolve(strict=True)
    except OSError as error:
        rot_say(f"Could not resolve project path:\n{error}")
        return 1
    if project in {context_root, project_context_root}:
        rot_say("The context storage directory cannot be used as a project.")
        return 1
    if not os.access(project, os.R_OK | os.X_OK):
        rot_say(f"Project directory is not readable:\n{project}")
        return 1

    try:
        target_config = config_path()
        load_config(target_config)
        binding = get_context_binding(name, target_config)
    except ConfigError as error:
        rot_say(str(error))
        return 1
    existing_source = binding.get("source_path")
    if existing_source and Path(existing_source).resolve() != project:
        rot_say(
            f"Context '{name}' already has a conflicting source binding:\n"
            f"{existing_source}"
        )
        return 1

    try:
        project, is_git_repo, remotes = inspect_source_project(project)
        synopsis, required_paths, optional_paths = _inspect_project(project, remotes)
        match_document = build_source_match_toml(
            is_git_repo,
            required_paths,
            optional_paths,
            remotes
        )
        definition = parse_match_toml(match_document)
        proposed_match = match_source_definition(project, name, definition)
    except (MatchError, ContextCreationError) as error:
        rot_say(str(error))
        return 1
    if not proposed_match.strong:
        rot_say("The generated match definition does not strongly match the project.")
        for evidence in proposed_match.evidence:
            marker = "+" if evidence.passed else "-"
            rot_continue(f"[{marker}] {evidence.message}")
        return 1

    context_id = new_context_id()
    documents = _placeholder_documents(name, definition)
    rot_say(f"Create context '{name}' from:\n\n  {project}")
    rot_continue(
        "Proposed files:\n\n"
        f"  {destination / 'metadata.toml'}\n"
        f"  {destination / 'local' / 'identity.md'}\n"
        f"  {destination / 'local' / 'state.md'}\n"
        f"  {destination / 'local' / 'vision.md'}\n"
        f"  {destination / 'local' / 'match.toml'}\n\n"
        "Local registration:\n\n"
        f"  {name}.source_path = {project}"
    )
    for filename, content in (
        ("identity.md", documents["identity"]),
        ("state.md", documents["state"]),
        ("vision.md", INITIAL_VISION),
        ("match.toml", match_document)
    ):
        rot_say(f"PROPOSED {filename}")
        rot_continue(content.rstrip())

    if not _confirm("Create this context and register its source path?"):
        rot_say("Context creation cancelled. No files or bindings were changed.")
        return 0

    if _destination_exists(destination):
        rot_say(f"Context '{name}' appeared before creation. Nothing was changed.")
        return 1
    try:
        proposed_match = match_source_definition(project, name, definition)
    except MatchError as error:
        rot_say(f"Project matching changed before creation:\n{error}")
        return 1
    if not proposed_match.strong:
        rot_say("Project no longer strongly matches the proposed match.toml.")
        return 1
    try:
        binding = get_context_binding(name, target_config)
    except ConfigError as error:
        rot_say(str(error))
        return 1
    existing_source = binding.get("source_path")
    if existing_source and Path(existing_source).resolve() != project:
        rot_say("The source binding changed before creation. Nothing was changed.")
        return 1

    try:
        _create_and_bind(
            name,
            context_id,
            destination,
            project,
            documents,
            match_document,
            target_config,
            definition
        )
    except ContextCreationError as error:
        rot_say(str(error))
        return 1

    rot_say(
        f"Context '{name}' created, validated, and its source path registered.\n"
        "Attempting optional AI enrichment..."
    )
    enrichment_error = _enrich_project_context(
        name,
        project,
        synopsis,
        destination,
        getattr(args, "agent", None),
        parent_command="context add"
    )
    if enrichment_error is not None:
        rot_say(
            f"AI enrichment warning:\n{enrichment_error}\n\n"
            "The context remains usable. Retry with:\n"
            f"  rot context develop {name}"
        )
    else:
        rot_say(f"Project context '{name}' enriched successfully.")
    return 0


def context_develop(args):
    name = getattr(args, "name", None)
    if not name:
        rot_say("A project context name is required.")
        return 1
    try:
        loader.load_context(name)
        definition = load_match_definition(name)
        binding = get_context_binding(name)
        source = binding.get("source_path")
        if source is None:
            raise ContextCreationError(
                f"Project context '{name}' does not have a local source binding."
            )
        project, _is_git_repo, remotes = inspect_source_project(source)
        matched = match_source_definition(project, name, definition)
        if not matched.strong:
            raise ContextCreationError(
                f"The bound source no longer strongly matches project context '{name}'."
            )
        synopsis, _required, _optional = _inspect_project(project, remotes)
        destination = loader.project_context_directory(name)
        enrichment_error = _enrich_project_context(
            name,
            project,
            synopsis,
            destination,
            getattr(args, "agent", None)
        )
    except (loader.ContextError, MatchError, ConfigError, ContextCreationError) as error:
        rot_say(str(error))
        return 1
    if enrichment_error is not None:
        rot_say(f"AI enrichment failed:\n{enrichment_error}\n\nThe context was not changed.")
        return 1
    rot_say(f"Project context '{name}' enriched successfully.")
    return 0
