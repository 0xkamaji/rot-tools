from datetime import datetime, timezone
import os
from pathlib import Path
import uuid

from rotbot.contexts import loader
from rotbot.contexts.people import PERSON_ROLES
from rotbot.contexts.config import (
    ConfigError,
    config_path,
    get_context_binding,
    load_config,
    remove_context_bindings
)
from rotbot.ui.terminal import rot_continue, rot_say


ARCHIVE_CATEGORY = ".archive"
ARCHIVE_CATEGORIES = {
    "project": "projects",
    "contact": "contacts",
    "user": "users",
    "assistant": "assistants"
}
CONTEXT_CATEGORIES = {"project": "projects", "person": "people"}


class ContextDeletionError(Exception):
    pass


def _exists(path):
    return os.path.lexists(path)


def _safe_directory(path, label):
    if path.is_symlink() or not path.is_dir():
        raise ContextDeletionError(f"Invalid {label} directory: {path}")


def _person_role_directories(context_root):
    people_root = context_root / "people"
    _safe_directory(people_root, "person context")
    directories = []
    for role in PERSON_ROLES:
        role_directory = people_root / role
        _safe_directory(role_directory, f"{role} person")
        directories.append((role, role_directory))
    return tuple(directories)


def _find_person_source(name, context_root):
    matches = []
    for role, role_directory in _person_role_directories(context_root):
        source = role_directory / name
        if _exists(source):
            matches.append((role, source))
    if len(matches) > 1:
        raise ContextDeletionError(
            f"Person context name exists in multiple role directories: {name}"
        )
    if not matches:
        return None
    _role, source = matches[0]
    if source.is_symlink() or not source.is_dir():
        raise ContextDeletionError(f"Invalid person context: {source}")
    return source


def _locate_context(name, context_root, context_type=None):
    try:
        loader.validate_context_name(name)
    except loader.ContextError as error:
        raise ContextDeletionError(str(error)) from None
    _safe_directory(context_root, "context root")

    if context_type is not None and context_type not in CONTEXT_CATEGORIES:
        raise ContextDeletionError(f"Unsupported context type: {context_type}")
    found = []
    categories = (
        ((context_type, CONTEXT_CATEGORIES[context_type]),)
        if context_type is not None
        else CONTEXT_CATEGORIES.items()
    )
    for found_type, category_name in categories:
        if found_type == "person":
            source = _find_person_source(name, context_root)
        else:
            category = context_root / category_name
            _safe_directory(category, f"{found_type} context")
            candidate = category / name
            source = candidate if _exists(candidate) else None
        if source is not None:
            found.append((found_type, source))
    if not found:
        raise ContextDeletionError(f"Context '{name}' does not exist.")
    if len(found) > 1:
        raise ContextDeletionError(
            f"Context name '{name}' is ambiguous; both a project and person exist."
        )
    context_type, source = found[0]
    if source.is_symlink() or not source.is_dir():
        raise ContextDeletionError(f"Invalid {context_type} context: {source}")
    return context_type, source


def list_deletable_contexts(*, context_root=None):
    context_root = loader.CONTEXT_ROOT if context_root is None else Path(context_root)
    _safe_directory(context_root, "context root")
    contexts = []
    for context_type, category_name in CONTEXT_CATEGORIES.items():
        categories = (
            _person_role_directories(context_root)
            if context_type == "person"
            else ((None, context_root / category_name),)
        )
        for _role, category in categories:
            _safe_directory(category, f"{context_type} context")
            try:
                entries = tuple(category.iterdir())
            except OSError as error:
                raise ContextDeletionError(
                    f"Could not list {context_type} contexts: {error}"
                ) from None
            for entry in entries:
                try:
                    loader.validate_context_name(entry.name)
                except loader.ContextError:
                    continue
                if not entry.is_symlink() and entry.is_dir():
                    contexts.append((context_type, entry.name))
    person_names = [name for context_type, name in contexts if context_type == "person"]
    duplicate = next((name for name in person_names if person_names.count(name) > 1), None)
    if duplicate is not None:
        raise ContextDeletionError(
            f"Person context name exists in multiple role directories: {duplicate}"
        )
    type_order = {name: index for index, name in enumerate(CONTEXT_CATEGORIES)}
    return tuple(sorted(contexts, key=lambda item: (type_order[item[0]], item[1])))


def _archive_category(context_type, source):
    category = context_type if context_type == "project" else source.parent.name
    try:
        return ARCHIVE_CATEGORIES[category]
    except KeyError:
        raise ContextDeletionError(f"Unsupported archive category: {category}") from None


def _ensure_archive_parent(context_root, archive_category, name):
    current = context_root
    for component in (ARCHIVE_CATEGORY, archive_category, name):
        current = current / component
        if _exists(current):
            _safe_directory(current, "archive")
        else:
            try:
                current.mkdir()
            except OSError as error:
                raise ContextDeletionError(f"Could not create archive directory: {error}") from None
    return current


def _archive_id():
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    return f"{timestamp}-{uuid.uuid4().hex}"


def archive_context(name, *, context_type=None, context_root=None, target_config=None):
    context_root = loader.CONTEXT_ROOT if context_root is None else Path(context_root)
    context_type, source = _locate_context(name, context_root, context_type)
    target_config = config_path() if target_config is None else Path(target_config)
    if context_type == "project":
        try:
            load_config(target_config)
            get_context_binding(name, target_config)
        except ConfigError as error:
            raise ContextDeletionError(str(error)) from None

    archive_category = _archive_category(context_type, source)
    archive_parent = _ensure_archive_parent(
        context_root,
        archive_category,
        name
    )
    while True:
        record = archive_parent / _archive_id()
        try:
            record.mkdir()
            break
        except FileExistsError:
            continue
        except OSError as error:
            raise ContextDeletionError(f"Could not create archive record: {error}") from None
    destination = record / "payload"
    try:
        os.rename(source, destination)
    except OSError as error:
        try:
            record.rmdir()
        except OSError:
            pass
        raise ContextDeletionError(f"Could not archive context '{name}': {error}") from None

    if context_type == "project":
        try:
            remove_context_bindings(name, target_config)
        except ConfigError as error:
            try:
                if _exists(source):
                    raise OSError("the original context path is no longer available")
                os.rename(destination, source)
                record.rmdir()
            except OSError as rollback_error:
                raise ContextDeletionError(
                    f"Could not remove bindings after archiving '{name}': {error}\n"
                    f"Rollback failed: {rollback_error}"
                ) from None
            raise ContextDeletionError(
                f"Could not remove bindings after archiving '{name}': {error}"
            ) from None
    return context_type, destination


def _confirm(message):
    rot_say(f"{message} [y/N]")
    try:
        answer = input("> ").strip().lower()
    except EOFError:
        answer = ""
    return answer in {"y", "yes"}


def _choose_context(contexts):
    exit_number = len(contexts) + 1
    rot_say(
        "Which context would you like to archive?\n\n"
        + "\n".join(
            f"  {index}. {context_type}: {name}"
            for index, (context_type, name) in enumerate(contexts, 1)
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
        if answer.isdigit() and 1 <= int(answer) <= len(contexts):
            return contexts[int(answer) - 1]
        rot_say(f"Please choose a number from 1 to {exit_number}.")


def context_delete(args):
    try:
        if args.name:
            context_type, source = _locate_context(args.name, loader.CONTEXT_ROOT)
            name = args.name
        else:
            contexts = list_deletable_contexts()
            if not contexts:
                rot_say("No contexts are available to archive.")
                return 1
            selected = _choose_context(contexts)
            if selected is None:
                rot_say("Context archival cancelled. No files or bindings were changed.")
                return 0
            context_type, name = selected
            context_type, source = _locate_context(
                name,
                loader.CONTEXT_ROOT,
                context_type
            )
    except ContextDeletionError as error:
        rot_say(str(error))
        return 1
    rot_say(f"Archive {context_type} context '{name}'?")
    archive_category = _archive_category(context_type, source)
    rot_continue(
        f"Move from:\n  {source}\n\n"
        f"Move under:\n  "
        f"{loader.CONTEXT_ROOT / ARCHIVE_CATEGORY / archive_category / name}\n\n"
        "Archived contexts are not loaded or matched by RotBot."
    )
    if not _confirm("Archive this context?"):
        rot_say("Context archival cancelled. No files or bindings were changed.")
        return 0
    try:
        archived_type, destination = archive_context(
            name,
            context_type=context_type
        )
    except ContextDeletionError as error:
        rot_say(str(error))
        return 1
    rot_say(
        f"{archived_type.title()} context '{name}' archived at:\n{destination}"
    )
    return 0
