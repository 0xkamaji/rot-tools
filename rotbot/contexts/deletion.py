from datetime import datetime, timezone
import os
from pathlib import Path
import uuid

from rotbot.contexts import loader
from rotbot.contexts.config import (
    ConfigError,
    config_path,
    get_context_binding,
    load_config,
    remove_context_bindings
)
from rotbot.ui.terminal import rot_continue, rot_say


ARCHIVE_CATEGORY = "archive"
CONTEXT_CATEGORIES = {"project": "projects", "person": "people"}


class ContextDeletionError(Exception):
    pass


def _exists(path):
    return os.path.lexists(path)


def _safe_directory(path, label):
    if path.is_symlink() or not path.is_dir():
        raise ContextDeletionError(f"Invalid {label} directory: {path}")


def _locate_context(name, context_root):
    try:
        loader.validate_context_name(name)
    except loader.ContextError as error:
        raise ContextDeletionError(str(error)) from None
    _safe_directory(context_root, "context root")

    found = []
    for context_type, category_name in CONTEXT_CATEGORIES.items():
        category = context_root / category_name
        _safe_directory(category, f"{context_type} context")
        source = category / name
        if _exists(source):
            found.append((context_type, source))
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


def _ensure_archive_parent(context_root, context_type, name):
    current = context_root
    for component in (ARCHIVE_CATEGORY, CONTEXT_CATEGORIES[context_type], name):
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


def archive_context(name, *, context_root=None, target_config=None):
    context_root = loader.CONTEXT_ROOT if context_root is None else Path(context_root)
    context_type, source = _locate_context(name, context_root)
    target_config = config_path() if target_config is None else Path(target_config)
    if context_type == "project":
        try:
            load_config(target_config)
            get_context_binding(name, target_config)
        except ConfigError as error:
            raise ContextDeletionError(str(error)) from None

    archive_parent = _ensure_archive_parent(context_root, context_type, name)
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


def context_delete(args):
    try:
        context_type, source = _locate_context(args.name, loader.CONTEXT_ROOT)
    except ContextDeletionError as error:
        rot_say(str(error))
        return 1
    rot_say(f"Archive {context_type} context '{args.name}'?")
    rot_continue(
        f"Move from:\n  {source}\n\n"
        f"Move under:\n  {loader.CONTEXT_ROOT / ARCHIVE_CATEGORY}\n\n"
        "Archived contexts are not loaded or matched by RotBot."
    )
    if not _confirm("Archive this context?"):
        rot_say("Context archival cancelled. No files or bindings were changed.")
        return 0
    try:
        archived_type, destination = archive_context(args.name)
    except ContextDeletionError as error:
        rot_say(str(error))
        return 1
    rot_say(
        f"{archived_type.title()} context '{args.name}' archived at:\n{destination}"
    )
    return 0
