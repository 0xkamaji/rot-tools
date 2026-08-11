from pathlib import Path
import re
from typing import NamedTuple

from gui import rot_continue, rot_say


CONTEXT_ROOT = Path(__file__).resolve().parent / "context"
CONTEXT_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class ContextError(Exception):
    pass


class Context(NamedTuple):
    name: str
    identity: str
    state: str


def validate_context_name(name):
    if not isinstance(name, str) or not CONTEXT_NAME_PATTERN.fullmatch(name):
        raise ContextError(f"Invalid context name: {name}")
    return name


def _context_paths(name):
    validate_context_name(name)

    root = CONTEXT_ROOT.resolve()
    directory = CONTEXT_ROOT / name
    try:
        resolved_directory = directory.resolve(strict=True)
    except OSError:
        raise ContextError(f"Unknown or invalid context: {name}") from None

    if (
        directory.is_symlink()
        or not resolved_directory.is_dir()
        or resolved_directory.parent != root
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


def list_contexts():
    try:
        entries = tuple(CONTEXT_ROOT.iterdir())
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
    identity_path, state_path = _context_paths(name)
    try:
        return Context(
            name=name,
            identity=identity_path.read_text(encoding="utf-8"),
            state=state_path.read_text(encoding="utf-8")
        )
    except (OSError, UnicodeError) as error:
        raise ContextError(f"Could not load context '{name}': {error}") from None


def load_vision(name):
    identity_path, _state_path = _context_paths(name)
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


def context_list(args):
    try:
        names = list_contexts()
    except ContextError as error:
        rot_say(str(error))
        return 1

    rot_say("CONTEXTS")
    rot_continue("\n".join(names) if names else "(none)")
    return 0


def context_show(args):
    if getattr(args, "vision", False):
        try:
            vision = load_vision(args.name)
        except ContextError as error:
            rot_say(str(error))
            return 1

        if vision is None:
            rot_say(f"No vision document exists for context '{args.name}'.")
            return 0

        rot_say(f"VISION: {args.name}")
        rot_continue(
            "Vision describes possible future direction. It is not current "
            "state, an approved requirement, or authorization to implement "
            "anything.\n\n"
            f"{vision}"
        )
        return 0

    try:
        context = load_context(args.name)
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
