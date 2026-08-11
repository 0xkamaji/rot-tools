import os
from pathlib import Path
import stat
import tempfile

from rotbot.contexts import loader, people
from rotbot.ui.terminal import rot_continue, rot_say


DOCUMENTS = {
    "identity.md": {
        "description": "Relatively stable information describing who the person is",
        "subject": "identity"
    },
    "preferences.md": {
        "description": "Preferences, habits, and ways of working",
        "subject": "preferences"
    },
    "relationship.md": {
        "description": "How the person relates to the active RotBot user",
        "subject": "relationship"
    },
    "state.md": {
        "description": "Current or temporary information that may change",
        "subject": "current state"
    },
    "experience.md": {
        "description": "Accumulated knowledge, abilities, and experience",
        "subject": "experience"
    },
    "priorities.md": {
        "description": "What currently matters and affects RotBot's assistance",
        "subject": "priorities"
    }
}


class PersonModificationError(Exception):
    pass


def available_documents(person):
    allowed = people.person_document_names(person)
    return tuple(filename for filename in DOCUMENTS if filename in allowed)


def _single_line(value, label, maximum):
    if not isinstance(value, str):
        raise PersonModificationError(f"Invalid {label}.")
    value = value.strip()
    if (
        not value
        or len(value) > maximum
        or any(ord(character) < 32 for character in value)
    ):
        raise PersonModificationError(f"Invalid {label}.")
    return value


def _document_headings(lines):
    headings = []
    fence = None
    for index, line in enumerate(lines):
        stripped = line.rstrip("\r\n")
        marker = stripped.lstrip()
        if marker.startswith("```") or marker.startswith("~~~"):
            current = marker[:3]
            if fence is None:
                fence = current
            elif fence == current:
                fence = None
            continue
        if fence is None and stripped.startswith("## "):
            category = stripped[3:].strip()
            if category:
                headings.append((category, index))
    names = [name for name, _index in headings]
    duplicate = next((name for name in names if names.count(name) > 1), None)
    if duplicate is not None:
        raise PersonModificationError(
            f"Duplicate person context category: {duplicate}"
        )
    return tuple(headings)


def _updated_document(content, category, information, category_description=None):
    lines = content.splitlines(keepends=True)
    headings = _document_headings(lines)
    positions = [index for name, index in headings if name == category]
    entry = f"- {information}"
    if not positions:
        if category_description is None:
            raise PersonModificationError(
                f"New person context category requires a description: {category}"
            )
        existing = content.rstrip()
        separator = "\n\n" if existing else ""
        return (
            existing
            + separator
            + f"## {category}\n\n<!-- {category_description} -->\n\n{entry}\n"
        )
    if category_description is not None:
        raise PersonModificationError(f"Person context category already exists: {category}")

    start = positions[0]
    end = next((index for _name, index in headings if index > start), len(lines))
    before = "".join(lines[:end]).rstrip()
    after = "".join(lines[end:]).lstrip("\r\n")
    updated = before + f"\n\n{entry}\n"
    if after:
        updated += "\n" + after
    return updated


def _person_document(name, filename, people_root):
    try:
        person = people.load_person_context(name, people_root=people_root)
    except people.PersonContextError as error:
        raise PersonModificationError(str(error)) from None
    if filename not in available_documents(person):
        raise PersonModificationError(
            f"Unsupported document for person context '{name}': {filename}"
        )
    document = people_root / person.name / filename
    if document.is_symlink() or not document.is_file():
        raise PersonModificationError(f"Invalid person context document: {filename}")
    return person, document


def person_document_categories(name, filename, *, people_root=None):
    root = Path(people_root) if people_root is not None else loader.CONTEXT_ROOT / "people"
    _person, document = _person_document(name, filename, root)
    try:
        content = document.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise PersonModificationError(f"Could not read person context document: {error}") from None
    return tuple(name for name, _index in _document_headings(content.splitlines()))


def add_person_information(
    name,
    filename,
    category,
    information,
    *,
    category_description=None,
    people_root=None
):
    root = Path(people_root) if people_root is not None else loader.CONTEXT_ROOT / "people"
    _person, document = _person_document(name, filename, root)
    category = _single_line(category, "person context category", 200)
    information = _single_line(information, "person context information", 10_000)
    if category_description is not None:
        category_description = _single_line(
            category_description,
            "person context category description",
            1_000
        )
        if "--" in category_description:
            raise PersonModificationError(
                "Invalid person context category description."
            )
    try:
        original_stat = document.stat()
        original = document.read_text(encoding="utf-8")
        updated = _updated_document(
            original,
            category,
            information,
            category_description
        )
    except (OSError, UnicodeError) as error:
        raise PersonModificationError(f"Could not read person context document: {error}") from None

    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=document.parent,
            prefix=f".{filename}.",
            suffix=".tmp",
            delete=False
        ) as temporary:
            temporary_path = Path(temporary.name)
            os.chmod(temporary_path, stat.S_IMODE(original_stat.st_mode))
            temporary.write(updated)
            temporary.flush()
            os.fsync(temporary.fileno())
        current_stat = document.stat()
        if (
            document.is_symlink()
            or current_stat.st_dev != original_stat.st_dev
            or current_stat.st_ino != original_stat.st_ino
            or current_stat.st_mtime_ns != original_stat.st_mtime_ns
            or current_stat.st_size != original_stat.st_size
        ):
            raise PersonModificationError(
                "Person context document changed before it could be updated."
            )
        os.replace(temporary_path, document)
        temporary_path = None
    except PersonModificationError:
        raise
    except OSError as error:
        raise PersonModificationError(f"Could not update person context document: {error}") from None
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except OSError:
                pass
    return document


def _read_input():
    try:
        return input("> ").strip()
    except EOFError:
        return None


def _choose_number(message, labels):
    rendered = message + "\n\n" + "\n".join(
        f"  {index}. {label}" for index, label in enumerate(labels, 1)
    )
    while True:
        rot_say(rendered)
        answer = _read_input()
        if answer is None:
            return None
        if answer.isdigit() and 1 <= int(answer) <= len(labels):
            return int(answer) - 1
        rot_say(f"Please choose a number from 1 to {len(labels)}.")


def _confirm(message):
    rot_say(f"{message} [y/N]")
    answer = _read_input()
    return answer is not None and answer.lower() in {"y", "yes"}


def context_mod(args):
    try:
        if args.name:
            person = people.load_person_context(args.name)
        else:
            person_contexts = people.list_person_contexts()
            if not person_contexts:
                rot_say("No person contexts are available to modify.")
                return 1
            choice = _choose_number(
                "Which person context would you like to modify?",
                tuple(
                    f"{person.name} ({person.display_name}, {person.role})"
                    for person in person_contexts
                )
            )
            if choice is None:
                rot_say("Context modification cancelled. No files were changed.")
                return 0
            person = person_contexts[choice]
    except people.PersonContextError as error:
        rot_say(str(error))
        return 1

    filenames = available_documents(person)
    file_choice = _choose_number(
        f"Which file would you like to add information to for {person.display_name}?",
        tuple(
            f"{filename} - {DOCUMENTS[filename]['description']}"
            for filename in filenames
        )
    )
    if file_choice is None:
        rot_say("Context modification cancelled. No files were changed.")
        return 0
    filename = filenames[file_choice]
    definition = DOCUMENTS[filename]
    try:
        categories = person_document_categories(person.name, filename)
    except PersonModificationError as error:
        rot_say(str(error))
        return 1
    category_choice = _choose_number(
        f"What would you like to add regarding their {definition['subject']}?",
        categories + ("Create a new category",)
    )
    if category_choice is None:
        rot_say("Context modification cancelled. No files were changed.")
        return 0
    if category_choice == len(categories):
        rot_say(
            "You are creating a new category.\n"
            "Enter its name. It will be added as a ## heading:"
        )
        category = _read_input()
        if category is None:
            rot_say("Context modification cancelled. No files were changed.")
            return 0
        rot_say(
            "Describe what information belongs in this category. "
            "This will be saved as its guidance comment:"
        )
        category_description = _read_input()
    else:
        category = categories[category_choice]
        category_description = None
    if category_description is None and category_choice == len(categories):
        rot_say("Context modification cancelled. No files were changed.")
        return 0

    rot_say("What information would you like to add?")
    information = _read_input()
    if information is None:
        rot_say("Context modification cancelled. No files were changed.")
        return 0
    try:
        category = _single_line(category, "person context category", 200)
        if category_description is not None:
            category_description = _single_line(
                category_description,
                "person context category description",
                1_000
            )
            if "--" in category_description:
                raise PersonModificationError(
                    "Invalid person context category description."
                )
        information = _single_line(information, "person context information", 10_000)
    except PersonModificationError as error:
        rot_say(str(error))
        return 1
    rot_say(f"Add information to person context '{person.name}'?")
    preview = f"File: {filename}\n\n## {category}\n\n"
    if category_description is not None:
        preview += f"<!-- {category_description} -->\n\n"
    preview += f"- {information}"
    rot_continue(preview)
    if not _confirm("Apply this context modification?"):
        rot_say("Context modification cancelled. No files were changed.")
        return 0
    try:
        destination = add_person_information(
            person.name,
            filename,
            category,
            information,
            category_description=category_description
        )
    except PersonModificationError as error:
        rot_say(str(error))
        return 1
    rot_say(f"Person context '{person.name}' updated:\n{destination}")
    return 0
