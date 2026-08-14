import os
from pathlib import Path
import re
import tempfile

from rotbot.contexts import documents, entities, loader, machines, people
from rotbot.contexts.inspection import inspect_current_context
from rotbot.session.last import LastResponseError, edit_text
from rotbot.ui.terminal import rot_say


MAX_LEARNED_ENTRY_BYTES = 64 * 1024
TARGETS = ("user", "assistant", "project", "machine", "contact")
DISCLOSURES = ("general", "private")
CATEGORY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
BACK = object()
EXIT = object()


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


def _resolve(target, inspected=None, reference=None, *, writable=False):
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
                if writable:
                    context, directory = entities.materialize_builtin_assistant(context.id)
                    return context, directory
                directory = entities._builtin_directory(
                    context.id, entities.ContextType.ASSISTANT
                )
                if directory is None:
                    raise LearningError(f"Unknown assistant context: {context.name}")
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


def _validate_document(content, filename):
    if "\0" in content:
        raise LearningError(f"{filename} contains a NUL byte.")
    return content


def _namespace_directory(directory, namespace):
    if namespace not in DISCLOSURES:
        raise LearningError(f"Unsupported knowledge disclosure: {namespace}")
    directory = Path(directory)
    try:
        documents.ensure_structure(directory)
    except documents.ContextDocumentError as error:
        raise LearningError(str(error)) from None
    return directory / namespace


def _category_filename(category):
    if (
        not isinstance(category, str)
        or not CATEGORY_PATTERN.fullmatch(category)
        or ".." in category
        or "/" in category
        or "\\" in category
        or any(ord(character) < 32 or ord(character) == 127 for character in category)
    ):
        raise LearningError(f"Invalid learning category: {category}")
    return f"{category}.md"


def _category_heading(category):
    return category.replace("-", " ").replace("_", " ").title()


def _category_path(directory, namespace, category):
    return _namespace_directory(directory, namespace) / _category_filename(category)


def _category_files(directory, namespace):
    try:
        return documents.namespace_files(directory, namespace)
    except documents.ContextDocumentError as error:
        raise LearningError(str(error)) from None


def _read(path):
    if path.is_symlink():
        raise LearningError(f"Knowledge document must not be a symlink: {path}")
    if not path.exists():
        return None, None
    if not path.is_file():
        raise LearningError(f"Invalid knowledge document: {path}")
    try:
        before = path.stat()
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise LearningError(f"Could not read knowledge document: {error}") from None
    return content, before


def _atomic_write(path, content, before):
    descriptor = None
    temporary = None
    try:
        descriptor, name = tempfile.mkstemp(
            prefix=f".{path.stem}-", suffix=".tmp", dir=path.parent
        )
        temporary = Path(name)
        if os.name != "nt":
            os.chmod(temporary, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as destination:
            descriptor = None
            destination.write(content)
            destination.flush()
            os.fsync(destination.fileno())
        if path.is_symlink():
            raise LearningError(f"{path.name} changed unexpectedly during update.")
        if before is None:
            if os.path.lexists(path):
                raise LearningError(f"{path.name} changed unexpectedly during creation.")
        else:
            current = path.stat()
            expected = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
            observed = (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns)
            if observed != expected:
                raise LearningError(f"{path.name} changed unexpectedly during update.")
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
        raise LearningError(f"Could not update knowledge document: {error}") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                pass


def learn_text(
    target, text, *, inspected=None, reference=None,
    namespace="private", category="learned"
):
    context, directory = _resolve(target, inspected, reference, writable=True)
    path = _append_knowledge(directory, namespace, category, text)
    return context, path


def _append_knowledge(directory, namespace, category, text):
    path = _category_path(directory, namespace, category)
    return _append_document(path, text)


def _append_document(path, text):
    try:
        if path.parent.is_symlink() or not path.parent.is_dir():
            raise LearningError(f"Invalid knowledge directory: {path.parent}")
        if os.name != "nt":
            os.chmod(path.parent, 0o700)
    except LearningError:
        raise
    except OSError as error:
        raise LearningError(f"Could not prepare knowledge directory: {error}") from None
    content, before = _read(path)
    base = (
        f"# {_category_heading(path.stem)}\n"
        if content is None else _validate_document(content, path.name)
    )
    if not base.strip():
        base = f"# {_category_heading(path.stem)}\n"
    updated = base.rstrip() + "\n\n" + _validate_entry(text)
    _atomic_write(path, updated, before)
    return path


def edit_learned(target, *, inspected=None, reference=None, editor=None):
    context, directory = _resolve(target, inspected, reference, writable=True)
    path = _category_path(directory, "private", "learned")
    _edit_document(path, editor)
    return context, path


def _edit_document(path, editor=None):
    content, before = _read(path)
    if content is None:
        raise LearningError(f"Knowledge document does not exist: {path.name}")
    original = _validate_document(content, path.name)
    try:
        edited = _validate_document((editor or edit_text)(original), path.name)
    except LastResponseError as error:
        raise LearningError(str(error)) from None
    _atomic_write(path, edited, before)
    return path


def show_learned(target, *, inspected=None, reference=None):
    context, directory = _resolve(target, inspected, reference)
    path = _category_path(directory, "private", "learned")
    content = _show_document(path)
    return context, path, content


def _show_document(path):
    content, _before = _read(path)
    if content is None:
        raise LearningError(f"Knowledge document does not exist: {path.name}")
    return _validate_document(content, path.name)


def _choose_disclosure():
    rot_say(
        "Knowledge space:\n\n"
        "  1. General\n"
        "  2. Private\n"
        "  3. Exit"
    )
    while True:
        try:
            answer = input("> ").strip().lower()
        except EOFError:
            return EXIT
        if answer in {"", "exit", "quit", "q", "3"}:
            return EXIT
        if answer in {"1", "general"}:
            return "general"
        if answer in {"2", "private"}:
            return "private"
        rot_say("Choose 1, 2, or 3.")


def _choose_category(directory, namespace, *, allow_new):
    files = _category_files(directory, namespace)
    options = [path.stem for path in files]
    if allow_new:
        options.append("New category")
    back_number = len(options) + 1
    exit_number = back_number + 1
    rot_say(
        f"{namespace.title()} categories:\n\n"
        + "\n".join(
            f"  {index}. {label}" for index, label in enumerate(options, 1)
        )
        + ("\n" if options else "")
        + f"  {back_number}. Back\n"
        + f"  {exit_number}. Exit"
    )
    while True:
        try:
            answer = input("> ").strip().lower()
        except EOFError:
            return EXIT
        if answer in {"exit", "quit", "q", str(exit_number)}:
            return EXIT
        if answer in {"", "back", "b", str(back_number)}:
            return BACK
        if answer.isdigit() and 1 <= int(answer) <= len(files):
            return files[int(answer) - 1]
        if allow_new and answer == str(len(files) + 1):
            rot_say("Enter a new category name:")
            try:
                category = input("> ").strip()
            except EOFError:
                return BACK
            filename = _category_filename(category)
            path = _namespace_directory(directory, namespace) / filename
            if os.path.lexists(path):
                raise LearningError(f"Learning category already exists: {category}")
            return path
        rot_say(f"Choose a number from 1 to {exit_number}.")


def _select_document(directory, *, allow_new):
    while True:
        namespace = _choose_disclosure()
        if namespace is EXIT:
            return EXIT
        selected = _choose_category(directory, namespace, allow_new=allow_new)
        if selected is BACK:
            continue
        return selected


def _writable_selection(target, context, directory, path):
    if target != "assistant" or entities.entity_directory(context).exists():
        return context, path
    relative = path.relative_to(directory)
    try:
        context, writable = entities.materialize_builtin_assistant(context.id)
    except entities.EntityContextError as error:
        raise LearningError(str(error)) from None
    return context, writable / relative


def learn_command(args):
    inspected = getattr(args, "inspected_context", None)
    action = args.learn_action
    target = args.learn_target
    reference = getattr(args, "name", None) if target == "contact" else None
    try:
        context, directory = _resolve(target, inspected, reference)
        path = _select_document(directory, allow_new=action == "append")
        if path is EXIT:
            rot_say("Learning cancelled. No files were changed.")
            return 0
        if action == "show":
            content = _show_document(path)
            print(content, end="" if content.endswith("\n") else "\n")
            return 0
        if action == "edit":
            context, path = _writable_selection(
                target, context, directory, path
            )
            _edit_document(path)
            rot_say(f"Updated knowledge for {target} '{context.name}':\n{path}")
            return 0
        if args.text:
            text = " ".join(args.text)
        else:
            rot_say(
                f"Learning target: {target} '{context.name}'\n"
                f"Category: {path.parent.name}/{path.name}\n"
                "Enter the exact text to learn:"
            )
            try:
                text = input("> ")
            except EOFError:
                raise LearningError("Learning cancelled.") from None
        _validate_entry(text)
        context, path = _writable_selection(target, context, directory, path)
        path = _append_document(path, text)
        rot_say(f"Learned for {target} '{context.name}':\n{path}")
        return 0
    except LearningError as error:
        rot_say(str(error))
        return 2
