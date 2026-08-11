import json
import os
from pathlib import Path
import re
import stat
import tempfile

from rotbot.agents.runner import stream_agent
from rotbot.contexts import loader
from rotbot.contexts.matching import (
    MatchError,
    build_source_match_document,
    inspect_source_repository,
    match_source_definition,
    parse_match_document
)
from rotbot.contexts.config import (
    ConfigError,
    config_path,
    get_context_binding,
    load_config,
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


def _required_paths(entries):
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
    return tuple(selected)


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
    required_paths = _required_paths(entries)
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
        f"Repository name: {project.name}\n"
        "Normalized Git remotes:\n"
        + "\n".join(f"- {remote}" for remote in remotes)
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
    return synopsis, required_paths


def _agent_prompt(name, synopsis):
    return (
        f"Draft context documents for the project named {name}. Use only the "
        "bounded synopsis below. Do not inspect or modify any files. Return only "
        "a JSON object with exactly two string keys: `identity` and `state`. "
        "Do not use a code fence or add commentary.\n\n"
        "The identity Markdown must begin with a level-one heading and describe "
        "stable facts: what the project is, its core purpose, intended role or "
        "audience, stable architecture, and repository identity when useful. "
        "Avoid marketing, invented history, temporary status, speculative plans, "
        "future vision, secrets, credentials, and machine-local absolute paths. "
        "State that identity is human-maintained and must not be rewritten "
        "automatically.\n\n"
        "The state Markdown must begin with a level-one heading and describe only "
        "what currently exists: major capabilities, structure, entry points, "
        "implemented integrations, commands, and directly evident limitations. "
        "Avoid roadmaps, speculation, secrets, credentials, and machine-local "
        "absolute paths. Draft only identity.md and state.md; do not draft "
        "match.md or vision.md.\n\n"
        f"PROJECT SYNOPSIS\n----------------\n{synopsis}"
    )


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
        documents[name] = content + "\n"
    if (
        "human-maintained" not in documents["identity"].lower()
        or "not be rewritten automatically" not in documents["identity"].lower()
    ):
        raise ContextCreationError(
            "Generated identity.md must preserve the human-maintained identity boundary."
        )
    return documents


def _confirm(message):
    rot_say(f"{message} [y/N]")
    try:
        answer = input("> ").strip().lower()
    except EOFError:
        answer = ""
    return answer in {"y", "yes"}


def _destination_exists(destination):
    return os.path.lexists(destination)


def _rollback_context(destination):
    errors = []
    for name in (
        "identity.md", "state.md", "match.md",
        ".identity.md.tmp", ".state.md.tmp", ".match.md.tmp"
    ):
        try:
            (destination / name).unlink(missing_ok=True)
        except OSError as error:
            errors.append(str(error))
    try:
        destination.rmdir()
    except OSError as error:
        errors.append(str(error))
    return tuple(errors)


def _write_document(path, content):
    with path.open("x", encoding="utf-8") as destination:
        destination.write(content)
        destination.flush()
        os.fsync(destination.fileno())


def _create_and_bind(name, destination, project, documents, match_document, target_config):
    created = False
    try:
        destination.mkdir()
        created = True
        files = {
            "identity.md": documents["identity"],
            "state.md": documents["state"],
            "match.md": match_document
        }
        for filename, content in files.items():
            _write_document(destination / f".{filename}.tmp", content)
        for filename in ("match.md", "identity.md", "state.md"):
            os.replace(destination / f".{filename}.tmp", destination / filename)

        current_binding = get_context_binding(name, target_config)
        current_source = current_binding.get("source_path")
        if current_source and Path(current_source).resolve() != project:
            raise ConfigError("The source binding changed during context creation.")
        set_context_binding(name, "source_path", str(project), target_config)
    except BaseException as error:
        rollback_errors = _rollback_context(destination) if created else ()
        if isinstance(error, (KeyboardInterrupt, SystemExit)):
            raise
        message = f"Could not create context '{name}':\n{error}"
        if rollback_errors:
            message += "\nRollback could not remove all new artifacts:\n" + "\n".join(
                rollback_errors
            )
        raise ContextCreationError(message) from None


def context_add(args):
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
        project, remotes = inspect_source_repository(project)
        synopsis, required_paths = _inspect_project(project, remotes)
        match_document = build_source_match_document(remotes, required_paths)
        definition = parse_match_document(match_document)
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

    prompt = _agent_prompt(name, synopsis)
    with tempfile.TemporaryDirectory(prefix="rotbot-context-agent-") as agent_directory:
        returncode, output, _elapsed = stream_agent(
            prompt,
            "Rotbot is still drafting context documents...",
            agent_directory,
            agent_name=getattr(args, "agent", None),
            timeout=300
        )
    if returncode != 0:
        rot_say(f"Context drafting failed with exit code {returncode}.")
        return returncode
    if not output.strip():
        rot_say("The AI agent returned no context documents.")
        return 1
    try:
        documents = _parse_agent_draft(output, project)
    except ContextCreationError as error:
        rot_say(str(error))
        return 1

    rot_say(f"Create context '{name}' from:\n\n  {project}")
    rot_continue(
        "Proposed files:\n\n"
        f"  context/projects/{name}/identity.md\n"
        f"  context/projects/{name}/state.md\n"
        f"  context/projects/{name}/match.md\n\n"
        "Local registration:\n\n"
        f"  {name}.source_path = {project}"
    )
    for filename, content in (
        ("identity.md", documents["identity"]),
        ("state.md", documents["state"]),
        ("match.md", match_document)
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
        rot_say("Project no longer strongly matches the proposed match.md.")
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
            destination,
            project,
            documents,
            match_document,
            target_config
        )
    except ContextCreationError as error:
        rot_say(str(error))
        return 1

    rot_say(
        f"Context '{name}' created and its source path registered.\n\n"
        "Optional next step:\n"
        f"  Create context/projects/{name}/vision.md manually."
    )
    return 0
