import json
import os
from pathlib import Path
from typing import NamedTuple

from rotbot.contexts import loader


PERSON_ROLES = ("contact", "user")
CORE_TEMPLATES = {
    "identity.md": (
        "# Identity\n\n"
        "<!-- Stable, relevant facts about this person. -->\n"
    ),
    "preferences.md": (
        "# Preferences\n\n"
        "<!-- Communication, collaboration, tools, habits, and relevant preferences. -->\n"
    ),
    "relationship.md": (
        "# Relationship\n\n"
        "<!-- How this person relates to the user and any useful shared history. -->\n"
    ),
    "state.md": (
        "# State\n\n"
        "<!-- Current, temporary, or ongoing information involving this person. -->\n"
    )
}
USER_TEMPLATES = {
    "experience.md": (
        "# Experience\n\n"
        "<!-- Skills, background, knowledge, and capabilities relevant to RotBot. -->\n"
    ),
    "priorities.md": (
        "# Priorities\n\n"
        "<!-- Current goals, responsibilities, and areas of focus. -->\n"
    )
}


class PersonContextError(Exception):
    pass


class PersonContext(NamedTuple):
    name: str
    role: str
    display_name: str


def build_person_context(name, role, display_name=None):
    try:
        loader.validate_context_name(name)
    except loader.ContextError as error:
        raise PersonContextError(str(error)) from None
    if role not in PERSON_ROLES:
        raise PersonContextError(f"Unsupported person role: {role}")
    if display_name is None:
        display_name = name
    if (
        not isinstance(display_name, str)
        or not display_name
        or any(ord(character) < 32 for character in display_name)
    ):
        raise PersonContextError("Invalid person display name.")
    return PersonContext(name=name, role=role, display_name=display_name)


def render_person_files(person):
    metadata = (
        'type = "person"\n'
        f"role = {json.dumps(person.role, ensure_ascii=False)}\n"
        f"name = {json.dumps(person.name, ensure_ascii=False)}\n"
        f"display_name = {json.dumps(person.display_name, ensure_ascii=False)}\n"
    )
    files = {"metadata.toml": metadata, **CORE_TEMPLATES}
    if person.role == "user":
        files.update(USER_TEMPLATES)
    return files


def _write_document(path, content):
    with path.open("x", encoding="utf-8") as destination:
        destination.write(content)
        destination.flush()
        os.fsync(destination.fileno())


def _rollback_person(destination, filenames):
    errors = []
    for filename in filenames:
        try:
            (destination / filename).unlink(missing_ok=True)
        except OSError as error:
            errors.append(str(error))
    try:
        destination.rmdir()
    except OSError as error:
        errors.append(str(error))
    return tuple(errors)


def create_person_context(name, role, display_name=None, *, people_root=None):
    person = build_person_context(name, role, display_name)
    files = render_person_files(person)
    root = Path(people_root) if people_root is not None else loader.CONTEXT_ROOT / "people"
    if root.is_symlink() or not root.is_dir():
        raise PersonContextError(f"Invalid people context directory: {root}")

    destination = root / person.name
    if os.path.lexists(destination):
        raise PersonContextError(f"Person context '{person.name}' already exists.")

    created = False
    try:
        destination.mkdir()
        created = True
        for filename, content in files.items():
            _write_document(destination / filename, content)
    except BaseException as error:
        rollback_errors = _rollback_person(destination, files) if created else ()
        if isinstance(error, (KeyboardInterrupt, SystemExit)):
            raise
        message = f"Could not create person context '{person.name}': {error}"
        if rollback_errors:
            message += "\nRollback could not remove all new artifacts:\n"
            message += "\n".join(rollback_errors)
        raise PersonContextError(message) from None
    return destination
