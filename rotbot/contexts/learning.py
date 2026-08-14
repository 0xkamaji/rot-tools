import os
from pathlib import Path
import tempfile

from rotbot.contexts import entities, loader, machines, people
from rotbot.contexts.inspection import inspect_current_context
from rotbot.session.last import LastResponseError, edit_text
from rotbot.ui.terminal import rot_say


LEARNED_TEMPLATE = "# Learned\n"
MAX_LEARNED_ENTRY_BYTES = 64 * 1024
TARGETS = ("user", "assistant", "project", "machine", "contact")


class LearningError(Exception):
    pass


def _available(target):
    try:
        if target == "user":
            return entities.list_user_contexts()
        if target == "assistant":
            return entities.list_assistant_contexts()
        if target == "project":
            return tuple(loader.load_context(name) for name in loader.list_contexts())
        if target == "machine":
            return machines.list_machine_contexts()
        return people.list_person_contexts()
    except (
        entities.EntityContextError, loader.ContextError,
        machines.MachineContextError, people.PersonContextError
    ) as error:
        raise LearningError(str(error)) from None


def _choose(target):
    contexts = _available(target)
    if not contexts:
        raise LearningError(f"No {target} contexts are available.")
    rot_say(
        f"Available {target} contexts:\n"
        + "\n".join(
            f"  {index}. {context.name}" for index, context in enumerate(contexts, 1)
        )
    )
    while True:
        try:
            answer = input("> ").strip()
        except EOFError:
            raise LearningError("Learning cancelled.") from None
        if answer.lower() in {"", "exit", "quit", "q"}:
            raise LearningError("Learning cancelled.")
        if answer.isdigit() and 1 <= int(answer) <= len(contexts):
            return contexts[int(answer) - 1]
        rot_say(f"Choose a number from 1 to {len(contexts)}, or exit.")


def _resolve(target, inspected=None, reference=None):
    if target not in TARGETS:
        raise LearningError(f"Unsupported learning target: {target}")
    if target == "contact":
        if not reference:
            raise LearningError("A contact name is required.")
        try:
            context = people.load_person_context_reference(reference, role="contact")
            return context, people.person_context_directory(context)
        except people.PersonContextError as error:
            raise LearningError(str(error)) from None

    inspected = inspect_current_context(bootstrap=False) if inspected is None else inspected
    current = getattr(inspected, f"{target}_id", None) or getattr(inspected, target, None)
    context = None
    try:
        if current is not None:
            if target == "user":
                context = entities.load_user_context(current)
            elif target == "assistant":
                context = entities.load_assistant_context(current)
            elif target == "project":
                context = loader.load_context_reference(current)
            else:
                context = machines.load_machine_context_reference(current)
        if context is None:
            context = _choose(target)
        if target in {"user", "assistant"}:
            if target == "assistant" and not entities.entity_directory(context).exists():
                context, directory = entities.materialize_builtin_assistant(context.id)
                return context, directory
            return context, entities.entity_directory(context)
        if target == "project":
            return context, loader.project_context_directory(context.name)
        return context, machines.machine_context_directory(context.name)
    except (
        entities.EntityContextError, loader.ContextError, machines.MachineContextError
    ) as error:
        raise LearningError(str(error)) from None


def _validate_entry(text):
    if not isinstance(text, str) or not text.strip():
        raise LearningError("Learned text must not be empty.")
    if len(text.encode("utf-8")) > MAX_LEARNED_ENTRY_BYTES:
        raise LearningError("Learned text is too large.")
    if any(
        (ord(character) < 32 and character not in {"\n", "\r", "\t"})
        or ord(character) == 127
        for character in text
    ):
        raise LearningError("Learned text contains unsafe control characters.")
    lines = text.strip().splitlines()
    return "- " + lines[0] + "".join(f"\n  {line}" for line in lines[1:]) + "\n"


def _validate_document(content):
    first = next((line.strip() for line in content.splitlines() if line.strip()), None)
    if first != "# Learned":
        raise LearningError("learned.md must begin with '# Learned'.")
    if "\0" in content:
        raise LearningError("learned.md contains a NUL byte.")
    return content


def _private_document(directory):
    directory = Path(directory)
    if directory.is_symlink() or not directory.is_dir():
        raise LearningError(f"Invalid context directory: {directory}")
    private = directory / "private"
    try:
        if not os.path.lexists(private):
            private.mkdir(mode=0o700)
        if private.is_symlink() or not private.is_dir():
            raise LearningError(f"Invalid private context directory: {private}")
        if os.name != "nt":
            os.chmod(private, 0o700)
    except LearningError:
        raise
    except OSError as error:
        raise LearningError(f"Could not prepare private context: {error}") from None
    return private / "learned.md"


def _read(path):
    if path.is_symlink():
        raise LearningError(f"learned.md must not be a symlink: {path}")
    if not path.exists():
        return None, None
    if not path.is_file():
        raise LearningError(f"Invalid learned.md: {path}")
    try:
        before = path.stat()
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise LearningError(f"Could not read learned.md: {error}") from None
    return content, before


def _atomic_write(path, content, before):
    descriptor = None
    temporary = None
    try:
        descriptor, name = tempfile.mkstemp(prefix=".learned-", suffix=".tmp", dir=path.parent)
        temporary = Path(name)
        if os.name != "nt":
            os.chmod(temporary, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as destination:
            descriptor = None
            destination.write(content)
            destination.flush()
            os.fsync(destination.fileno())
        if path.is_symlink():
            raise LearningError("learned.md changed unexpectedly during update.")
        if before is None:
            if os.path.lexists(path):
                raise LearningError("learned.md changed unexpectedly during creation.")
        else:
            current = path.stat()
            expected = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
            observed = (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns)
            if observed != expected:
                raise LearningError("learned.md changed unexpectedly during update.")
        if before is None:
            os.link(temporary, path)
            temporary.unlink()
            temporary = None
        else:
            os.replace(temporary, path)
            temporary = None
        if os.name != "nt":
            os.chmod(path, 0o600)
    except LearningError:
        raise
    except OSError as error:
        raise LearningError(f"Could not update learned.md: {error}") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                pass


def learn_text(target, text, *, inspected=None, reference=None):
    context, directory = _resolve(target, inspected, reference)
    path = _append_learned(directory, text)
    return context, path


def _append_learned(directory, text):
    path = _private_document(directory)
    content, before = _read(path)
    base = LEARNED_TEMPLATE if content is None else _validate_document(content)
    updated = base.rstrip() + "\n\n" + _validate_entry(text)
    _atomic_write(path, updated, before)
    return path


def edit_learned(target, *, inspected=None, reference=None, editor=None):
    context, directory = _resolve(target, inspected, reference)
    path = _private_document(directory)
    content, before = _read(path)
    original = LEARNED_TEMPLATE if content is None else _validate_document(content)
    try:
        edited = _validate_document((editor or edit_text)(original))
    except LastResponseError as error:
        raise LearningError(str(error)) from None
    _atomic_write(path, edited, before)
    return context, path


def show_learned(target, *, inspected=None, reference=None):
    context, directory = _resolve(target, inspected, reference)
    path = _private_document(directory)
    content, _before = _read(path)
    if content is None:
        raise LearningError(
            f"No learned knowledge is available for {target} '{context.name}'."
        )
    return context, path, _validate_document(content)


def learn_command(args):
    inspected = getattr(args, "inspected_context", None)
    action = args.learn_action
    target = args.learn_target
    reference = getattr(args, "name", None) if target == "contact" else None
    try:
        if action == "show":
            _context, _path, content = show_learned(
                target, inspected=inspected, reference=reference
            )
            print(content, end="" if content.endswith("\n") else "\n")
            return 0
        if action == "edit":
            context, path = edit_learned(
                target, inspected=inspected, reference=reference
            )
            rot_say(f"Updated learned knowledge for {target} '{context.name}':\n{path}")
            return 0
        context, directory = _resolve(target, inspected, reference)
        if args.text:
            text = " ".join(args.text)
        else:
            rot_say(
                f"Learning target: {target} '{context.name}'\n"
                "Enter the exact text to append to private/learned.md:"
            )
            try:
                text = input("> ")
            except EOFError:
                raise LearningError("Learning cancelled.") from None
        path = _append_learned(directory, text)
        rot_say(f"Learned for {target} '{context.name}':\n{path}")
        return 0
    except LearningError as error:
        rot_say(str(error))
        return 2
