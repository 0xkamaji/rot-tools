import json
import os
from pathlib import Path
import tomllib
from typing import NamedTuple
import shutil

from rotbot.contexts import documents, loader
from rotbot.contexts.identifiers import (
    ContextIdentifierError,
    legacy_context_id,
    new_context_id,
    validate_context_id
)


PERSON_ROLES = ("contact", "user", "assistant")


class PersonContextError(Exception):
    pass


class PersonContext(NamedTuple):
    name: str
    role: str
    display_name: str
    related_projects: tuple = ()
    id: str | None = None


class PersonDocument(NamedTuple):
    filename: str
    sections: tuple


def _people_root(people_root=None):
    if people_root is None:
        raise PersonContextError("A legacy people root must be provided explicitly.")
    root = Path(people_root)
    if root.is_symlink() or not root.is_dir():
        raise PersonContextError(f"Invalid people context directory: {root}")
    for role in PERSON_ROLES:
        role_root = root / role
        if role_root.is_symlink() or not role_root.is_dir():
            raise PersonContextError(f"Invalid {role} person directory: {role_root}")
    return root


def _contacts_root():
    root = loader.CONTEXT_ROOT / "contacts"
    if root.is_symlink():
        raise PersonContextError(f"Invalid contact context directory: {root}")
    return root


def _canonical_contact_directory(name):
    try:
        loader.validate_context_name(name)
    except loader.ContextError as error:
        raise PersonContextError(str(error)) from None
    directory = _contacts_root() / name
    try:
        documents.recover_interrupted_migration(directory)
    except documents.ContextDocumentError as error:
        raise PersonContextError(str(error)) from None
    if directory.is_symlink() or not directory.is_dir():
        raise PersonContextError(f"Unknown or invalid person context: {name}")
    return directory


def _find_person_directory(name, root):
    try:
        loader.validate_context_name(name)
    except loader.ContextError as error:
        raise PersonContextError(str(error)) from None
    matches = []
    for role in PERSON_ROLES:
        directory = root / role / name
        if os.path.lexists(directory):
            matches.append((role, directory))
    if not matches:
        raise PersonContextError(f"Unknown or invalid person context: {name}")
    if len(matches) > 1:
        raise PersonContextError(
            f"Person context name exists in multiple role directories: {name}"
        )
    role, directory = matches[0]
    if directory.is_symlink() or not directory.is_dir():
        raise PersonContextError(f"Unknown or invalid person context: {name}")
    return role, directory


def person_context_directory(person, *, people_root=None):
    if people_root is None and person.role == "contact":
        return _canonical_contact_directory(person.name)
    root = _people_root(people_root)
    role, directory = _find_person_directory(person.name, root)
    if role != person.role:
        raise PersonContextError(f"Person role directory does not match metadata: {person.name}")
    return directory


def _normalize_related_projects(related_projects):
    if related_projects is None:
        return ()
    if isinstance(related_projects, (str, bytes)):
        raise PersonContextError("Related projects must be a collection of names.")
    try:
        projects = tuple(related_projects)
    except TypeError:
        raise PersonContextError("Related projects must be a collection of names.") from None
    normalized = []
    seen = set()
    for project in projects:
        if not isinstance(project, str):
            raise PersonContextError("Related project names must be strings.")
        try:
            loader.validate_context_name(project)
        except loader.ContextError:
            raise PersonContextError(f"Invalid related project name: {project}") from None
        if project not in seen:
            normalized.append(project)
            seen.add(project)
    return tuple(normalized)


def build_person_context(
    name,
    role,
    display_name=None,
    related_projects=None,
    context_id=None
):
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
        or len(display_name) > 200
        or any(ord(character) < 32 for character in display_name)
    ):
        raise PersonContextError("Invalid person display name.")
    try:
        normalized_id = validate_context_id(context_id or new_context_id())
    except ContextIdentifierError as error:
        raise PersonContextError(str(error)) from None
    return PersonContext(
        name=name,
        role=role,
        display_name=display_name,
        related_projects=_normalize_related_projects(related_projects),
        id=normalized_id
    )


def render_person_files(person):
    metadata = (
        'type = "person"\n'
        f"id = {json.dumps(person.id, ensure_ascii=False)}\n"
        f"role = {json.dumps(person.role, ensure_ascii=False)}\n"
        f"name = {json.dumps(person.name, ensure_ascii=False)}\n"
        f"display_name = {json.dumps(person.display_name, ensure_ascii=False)}\n"
        "related_projects = "
        f"{json.dumps(list(person.related_projects), ensure_ascii=False)}\n"
    )
    return {
        "metadata.toml": metadata,
        "identity.md": documents.render_identity(
            person.name, person.role, person.display_name
        ),
        "relationships.toml": documents.render_relationships(person.related_projects)
    }


def _strip_markdown_comment(line, in_comment):
    output = ""
    remaining = line
    while remaining:
        if in_comment:
            end = remaining.find("-->")
            if end < 0:
                return output, True
            remaining = remaining[end + 3:]
            in_comment = False
            continue
        start = remaining.find("<!--")
        if start < 0:
            return output + remaining, False
        output += remaining[:start]
        remaining = remaining[start + 4:]
        in_comment = True
    return output, in_comment


def populated_markdown_sections(markdown, filename):
    sections = []
    heading = None
    body = []
    in_comment = False
    fence = None

    def finish_section():
        content = "".join(body).strip()
        if content:
            sections.append((heading, content))

    for raw_line in markdown.splitlines(keepends=True):
        stripped = raw_line.strip()
        if fence is not None:
            body.append(raw_line)
            if stripped.startswith(fence):
                fence = None
            continue

        line, in_comment = _strip_markdown_comment(raw_line, in_comment)
        stripped = line.strip()
        if in_comment and not stripped:
            continue
        if stripped.startswith("```") or stripped.startswith("~~~"):
            fence = stripped[:3]
            body.append(line)
            continue
        if line.startswith("## "):
            finish_section()
            heading = line[3:].strip()
            body = []
            continue
        if heading is None and line.startswith("# ") and not "".join(body).strip():
            continue
        body.append(line)
    if in_comment:
        raise PersonContextError(f"Unterminated Markdown comment: {filename}")
    if fence is not None:
        raise PersonContextError(f"Unterminated Markdown fence: {filename}")
    finish_section()
    return tuple(sections)


def load_person_documents(name, *, people_root=None, view="full"):
    root = _people_root(people_root) if people_root is not None else None
    person = load_person_context(name, people_root=root)
    directory = person_context_directory(person, people_root=root)
    loaded = []
    try:
        paths = documents.semantic_files(directory, view)
        for path in paths:
            content = path.read_text(encoding="utf-8")
            loaded.append(PersonDocument(
                path.name, populated_markdown_sections(content, path.name)
            ))
    except (OSError, UnicodeError, documents.ContextDocumentError) as error:
        raise PersonContextError(str(error)) from None
    return person, tuple(loaded)


def load_person_context(name, *, people_root=None):
    if people_root is None:
        directory = _canonical_contact_directory(name)
        directory_role = "contact"
        try:
            documents.ensure_structure(directory, directory_role)
        except documents.ContextDocumentError as error:
            raise PersonContextError(str(error)) from None
        metadata_path = directory / "metadata.toml"
        try:
            metadata = tomllib.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, tomllib.TOMLDecodeError) as error:
            raise PersonContextError(
                f"Could not load person context '{name}': {error}"
            ) from None
        person = build_person_context(
            metadata.get("name"), metadata.get("role"),
            metadata.get("display_name"), metadata.get("related_projects", []),
            metadata.get("id") or legacy_context_id("person", name)
        )
        if metadata.get("type") != "person" or person.role != directory_role:
            raise PersonContextError(f"Invalid person metadata: {name}")
        return person
    root = _people_root(people_root)
    directory_role, directory = _find_person_directory(name, root)
    try:
        documents.ensure_structure(directory, directory_role)
    except documents.ContextDocumentError as error:
        raise PersonContextError(str(error)) from None
    metadata_path = directory / "metadata.toml"
    if (
        directory.is_symlink()
        or not directory.is_dir()
        or metadata_path.is_symlink()
        or not metadata_path.is_file()
    ):
        raise PersonContextError(f"Unknown or invalid person context: {name}")
    try:
        metadata = tomllib.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as error:
        raise PersonContextError(
            f"Could not load person context '{name}': {error}"
        ) from None
    if (
        metadata.get("type") != "person"
        or metadata.get("name") != name
        or "display_name" not in metadata
    ):
        raise PersonContextError(f"Invalid person metadata: {name}")
    related_projects = metadata.get("related_projects", [])
    if not isinstance(related_projects, list):
        raise PersonContextError(f"Invalid related projects metadata: {name}")
    person = build_person_context(
        metadata.get("name"),
        metadata.get("role"),
        metadata.get("display_name"),
        related_projects,
        metadata.get("id") or legacy_context_id("person", name)
    )
    if person.role != directory_role:
        raise PersonContextError(f"Person role directory does not match metadata: {name}")
    for filename in render_person_files(person):
        if person.role == "user" and filename == "identity.md":
            continue
        document = directory / filename
        if document.is_symlink() or not document.is_file():
            raise PersonContextError(f"Invalid person document: {name}/{filename}")
    return person


def list_person_contexts(*, people_root=None):
    if people_root is None:
        root = _contacts_root()
        if not root.exists():
            return ()
        if root.is_symlink() or not root.is_dir():
            raise PersonContextError(f"Invalid contact context directory: {root}")
        try:
            documents.recover_interrupted_migrations(root)
        except documents.ContextDocumentError as error:
            raise PersonContextError(str(error)) from None
        contexts = []
        for entry in root.iterdir():
            try:
                contexts.append(load_person_context(entry.name))
            except PersonContextError:
                continue
        return tuple(sorted(contexts, key=lambda person: person.name))
    root = _people_root(people_root)
    entries = []
    for role in PERSON_ROLES:
        try:
            entries.extend((role, entry) for entry in (root / role).iterdir())
        except OSError as error:
            raise PersonContextError(f"Could not list person contexts: {error}") from None
    names = [entry.name for _role, entry in entries if entry.is_dir() and not entry.is_symlink()]
    duplicate = next((name for name in names if names.count(name) > 1), None)
    if duplicate is not None:
        raise PersonContextError(
            f"Person context name exists in multiple role directories: {duplicate}"
        )
    person_contexts = []
    for _role, entry in entries:
        try:
            person_contexts.append(load_person_context(entry.name, people_root=root))
        except PersonContextError:
            continue
    return tuple(sorted(person_contexts, key=lambda person: person.name))


def load_person_context_reference(reference, role=None, *, people_root=None):
    matches = tuple(
        person
        for person in list_person_contexts(people_root=people_root)
        if reference in {person.id, person.name} and role in {None, person.role}
    )
    if not matches:
        raise PersonContextError(f"Unknown {role or 'person'} context reference: {reference}")
    if len(matches) > 1:
        raise PersonContextError(f"Ambiguous person context reference: {reference}")
    return matches[0]


def _write_document(path, content):
    with path.open("x", encoding="utf-8") as destination:
        destination.write(content)
        destination.flush()
        os.fsync(destination.fileno())
    if os.name != "nt":
        os.chmod(path, 0o600)


def create_person_context(
    name,
    role,
    display_name=None,
    related_projects=None,
    context_id=None,
    *,
    people_root=None
):
    try:
        person = build_person_context(
            name,
            role,
            display_name,
            related_projects,
            context_id
        )
    except ContextIdentifierError as error:
        raise PersonContextError(str(error)) from None
    files = render_person_files(person)
    if people_root is None and role == "contact":
        root = _contacts_root()
        root.mkdir(parents=True, mode=0o700, exist_ok=True)
        destination = root / person.name
        if os.path.lexists(destination):
            raise PersonContextError(f"Person context '{person.name}' already exists.")
        try:
            destination.mkdir(mode=0o700)
            _write_document(destination / "metadata.toml", files.pop("metadata.toml"))
            _write_document(destination / "identity.md", files.pop("identity.md"))
            _write_document(
                destination / "relationships.toml", files.pop("relationships.toml")
            )
            (destination / "general").mkdir(mode=0o700)
            (destination / "private").mkdir(mode=0o700)
        except BaseException as error:
            shutil.rmtree(destination, ignore_errors=True)
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise
            raise PersonContextError(
                f"Could not create person context '{person.name}': {error}"
            ) from None
        return destination
    root = _people_root(people_root)

    destinations = tuple(root / role / person.name for role in PERSON_ROLES)
    if any(os.path.lexists(destination) for destination in destinations):
        raise PersonContextError(f"Person context '{person.name}' already exists.")
    destination = root / person.role / person.name

    created = False
    try:
        destination.mkdir()
        created = True
        _write_document(destination / "metadata.toml", files.pop("metadata.toml"))
        identity = files.pop("identity.md")
        _write_document(
            destination / "relationships.toml", files.pop("relationships.toml")
        )
        (destination / "general").mkdir(mode=0o700)
        (destination / "private").mkdir(mode=0o700)
        if person.role == "user":
            _write_document(destination / "general" / "identity.md", identity)
            _write_document(destination / "private" / "identity.md", identity)
        else:
            _write_document(destination / "identity.md", identity)
    except BaseException as error:
        rollback_errors = ()
        if created:
            try:
                shutil.rmtree(destination)
            except OSError as rollback_error:
                rollback_errors = (str(rollback_error),)
        if isinstance(error, (KeyboardInterrupt, SystemExit)):
            raise
        message = f"Could not create person context '{person.name}': {error}"
        if rollback_errors:
            message += "\nRollback could not remove all new artifacts:\n"
            message += "\n".join(rollback_errors)
        raise PersonContextError(message) from None
    return destination
