from dataclasses import dataclass
from enum import Enum
import json
import os
from pathlib import Path
import shutil
import tempfile
import tomllib
import uuid

from rotbot.contexts import loader, people
from rotbot.contexts.identifiers import (
    ContextIdentifierError,
    legacy_context_id,
    new_context_id,
    validate_context_id
)


class ContextType(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"
    MACHINE = "machine"
    PROJECT = "project"


CONTEXT_TYPES = {
    ContextType.USER: "users",
    ContextType.ASSISTANT: "assistants",
    ContextType.MACHINE: "machines",
    ContextType.PROJECT: "projects"
}

USER_DOCUMENTS = (
    "identity.md", "preferences.md", "experience.md", "priorities.md",
    "relationship.md", "state.md"
)
ASSISTANT_DOCUMENTS = (
    "identity.md", "behavior.md", "relationship.md", "state.md"
)

BEHAVIOR_TEMPLATE = (
    "# Behavior\n\n"
    "<!-- Durable behavioral preferences for this assistant. -->\n\n"
    "## Communication\n\n"
    "<!-- Tone, level of detail, and presentation style. -->\n\n"
    "## Collaboration\n\n"
    "<!-- How the assistant works with users and handles uncertainty. -->\n\n"
    "## Limitations\n\n"
    "<!-- How the assistant communicates limits, risk, and uncertainty. -->\n\n"
    "## Other\n\n"
    "<!-- Other durable behavioral guidance. -->\n"
)

SAFE_CAPABILITIES = """[interaction]
default_mode = "talk"

[modes.talk]
enabled = true

[modes.work]
enabled = false
scope = "active_project"

[transitions]
revoke_work_on_project_change = true
"""


class EntityContextError(Exception):
    pass


@dataclass(frozen=True)
class UserContext:
    name: str
    display_name: str
    related_projects: tuple[str, ...]
    id: str

    @property
    def context_type(self):
        return ContextType.USER

    @property
    def role(self):
        return "user"


@dataclass(frozen=True)
class AssistantContext:
    name: str
    display_name: str
    related_projects: tuple[str, ...]
    id: str

    @property
    def context_type(self):
        return ContextType.ASSISTANT

    @property
    def role(self):
        return "assistant"


EntityDocument = people.PersonDocument


def context_root(context_type, root=None):
    context_type = ContextType(context_type)
    base = loader.CONTEXT_ROOT if root is None else Path(root)
    return base / CONTEXT_TYPES[context_type]


def _legacy_root(context_type, root=None):
    base = loader.CONTEXT_ROOT if root is None else Path(root)
    return base / "people" / ContextType(context_type).value


def _document_names(context_type):
    return USER_DOCUMENTS if ContextType(context_type) == ContextType.USER else ASSISTANT_DOCUMENTS


def _normalize_related_projects(projects):
    try:
        return people._normalize_related_projects(projects)
    except people.PersonContextError as error:
        raise EntityContextError(str(error)) from None


def build_user_context(name, display_name=None, related_projects=None, context_id=None):
    return _build_entity(
        ContextType.USER, name, display_name, related_projects, context_id
    )


def build_assistant_context(name, display_name=None, related_projects=None, context_id=None):
    return _build_entity(
        ContextType.ASSISTANT, name, display_name, related_projects, context_id
    )


def _build_entity(context_type, name, display_name, related_projects, context_id):
    try:
        loader.validate_context_name(name)
        context_id = validate_context_id(context_id or new_context_id())
    except (loader.ContextError, ContextIdentifierError) as error:
        raise EntityContextError(str(error)) from None
    display_name = name if display_name is None else display_name
    if (
        not isinstance(display_name, str)
        or not display_name
        or any(ord(character) < 32 for character in display_name)
    ):
        raise EntityContextError(f"Invalid {context_type.value} display name.")
    entity_type = UserContext if context_type == ContextType.USER else AssistantContext
    return entity_type(
        name, display_name, _normalize_related_projects(related_projects), context_id
    )


def render_metadata(entity):
    return (
        f'type = {json.dumps(entity.context_type.value)}\n'
        f'id = {json.dumps(entity.id)}\n'
        f'name = {json.dumps(entity.name, ensure_ascii=False)}\n'
        f'display_name = {json.dumps(entity.display_name, ensure_ascii=False)}\n'
        f'related_projects = {json.dumps(list(entity.related_projects), ensure_ascii=False)}\n'
    )


def render_entity_files(entity):
    if isinstance(entity, UserContext):
        templates = {
            **people.CORE_TEMPLATES,
            **people.USER_TEMPLATES
        }
    elif isinstance(entity, AssistantContext):
        templates = {
            "identity.md": people.CORE_TEMPLATES["identity.md"],
            "behavior.md": BEHAVIOR_TEMPLATE,
            "relationship.md": people.CORE_TEMPLATES["relationship.md"],
            "state.md": people.CORE_TEMPLATES["state.md"],
            "capabilities.toml": SAFE_CAPABILITIES
        }
    else:
        raise EntityContextError(f"Unsupported entity context: {entity!r}")
    return {"metadata.toml": render_metadata(entity), **templates}


def entity_directory(entity, root=None):
    return context_root(entity.context_type, root) / entity.name


def _canonical_directory(reference, context_type, root=None):
    category = context_root(context_type, root)
    if not os.path.lexists(category):
        return None
    if category.is_symlink() or not category.is_dir():
        raise EntityContextError(
            f"Invalid {context_type.value} context directory: {category}"
        )
    try:
        uuid.UUID(str(reference))
        reference_is_id = True
    except (ValueError, TypeError, AttributeError):
        reference_is_id = False
    direct = category / str(reference)
    if not reference_is_id and os.path.lexists(direct):
        if direct.is_symlink() or not direct.is_dir():
            raise EntityContextError(
                f"Unknown or invalid {context_type.value} context: {reference}"
            )
        return direct
    matches = []
    try:
        entries = tuple(category.iterdir())
    except OSError as error:
        raise EntityContextError(
            f"Could not list {context_type.value} contexts: {error}"
        ) from None
    for entry in entries:
        if entry.is_symlink() or not entry.is_dir():
            continue
        try:
            metadata = _read_metadata(entry, context_type)
        except EntityContextError:
            continue
        if metadata.get("id") == reference:
            matches.append(entry)
    if len(matches) > 1:
        raise EntityContextError(
            f"Ambiguous {context_type.value} context reference: {reference}"
        )
    return matches[0] if matches else None


def _read_metadata(directory, context_type):
    path = directory / "metadata.toml"
    try:
        if path.is_symlink() or not path.is_file():
            raise EntityContextError(f"Invalid {context_type.value} metadata: {directory.name}")
        metadata = tomllib.loads(path.read_text(encoding="utf-8"))
    except EntityContextError:
        raise
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as error:
        raise EntityContextError(
            f"Could not load {context_type.value} context '{directory.name}': {error}"
        ) from None
    if metadata.get("type") != context_type.value or metadata.get("name") != directory.name:
        raise EntityContextError(f"Invalid {context_type.value} metadata: {directory.name}")
    return metadata


def _load_canonical(reference, context_type, root=None):
    directory = _canonical_directory(reference, context_type, root)
    if directory is None:
        return None
    metadata = _read_metadata(directory, context_type)
    builder = build_user_context if context_type == ContextType.USER else build_assistant_context
    entity = builder(
        metadata.get("name"), metadata.get("display_name"),
        metadata.get("related_projects", []), metadata.get("id")
    )
    for filename in _document_names(context_type):
        path = directory / filename
        if path.is_symlink() or not path.is_file():
            raise EntityContextError(
                f"Invalid {context_type.value} document: {entity.name}/{filename}"
            )
    if context_type == ContextType.ASSISTANT:
        capabilities = directory / "capabilities.toml"
        if capabilities.is_symlink() or not capabilities.is_file():
            raise EntityContextError(
                f"Invalid assistant capabilities: {entity.name}/capabilities.toml"
            )
    return entity


def _load_legacy(reference, context_type, root=None):
    legacy = _legacy_root(context_type, root)
    if legacy.is_symlink() or not legacy.is_dir():
        raise EntityContextError(
            f"Unknown {context_type.value} context reference: {reference}"
        )
    matches = []
    for entry in legacy.iterdir():
        if entry.is_symlink() or not entry.is_dir():
            continue
        try:
            person = people.load_person_context(
                entry.name, people_root=legacy.parent
            )
        except people.PersonContextError:
            continue
        if person.role == context_type.value and reference in {person.name, person.id}:
            matches.append(person)
    if len(matches) != 1:
        label = "Ambiguous" if matches else "Unknown"
        raise EntityContextError(
            f"{label} {context_type.value} context reference: {reference}"
        )
    person = matches[0]
    builder = build_user_context if context_type == ContextType.USER else build_assistant_context
    return builder(
        person.name, person.display_name, person.related_projects, person.id
    )


def load_user_context(reference, *, root=None):
    return _load_canonical(reference, ContextType.USER, root) or _load_legacy(
        reference, ContextType.USER, root
    )


def load_assistant_context(reference, *, root=None):
    return _load_canonical(reference, ContextType.ASSISTANT, root) or _load_legacy(
        reference, ContextType.ASSISTANT, root
    )


def _list(context_type, root=None):
    category = context_root(context_type, root)
    canonical = []
    if category.is_dir() and not category.is_symlink():
        for entry in category.iterdir():
            try:
                canonical.append(_load_canonical(entry.name, context_type, root))
            except EntityContextError:
                continue
    by_id = {entity.id: entity for entity in canonical}
    legacy = _legacy_root(context_type, root)
    if legacy.is_dir() and not legacy.is_symlink():
        try:
            people_contexts = people.list_person_contexts(people_root=legacy.parent)
        except people.PersonContextError:
            people_contexts = ()
        for person in people_contexts:
            if person.role != context_type.value or person.id in by_id:
                continue
            builder = build_user_context if context_type == ContextType.USER else build_assistant_context
            by_id[person.id] = builder(
                person.name, person.display_name, person.related_projects, person.id
            )
    return tuple(sorted(by_id.values(), key=lambda item: item.name))


def list_user_contexts(*, root=None):
    return _list(ContextType.USER, root)


def list_assistant_contexts(*, root=None):
    return _list(ContextType.ASSISTANT, root)


def load_entity_documents(entity, *, root=None):
    directory = entity_directory(entity, root)
    if directory.exists():
        metadata = _read_metadata(directory, entity.context_type)
        if metadata.get("id") != entity.id:
            raise EntityContextError(
                f"Canonical and legacy {entity.context_type.value} contexts conflict: "
                f"{entity.name}"
            )
    else:
        directory = _legacy_root(entity.context_type, root) / entity.name
    documents = []
    for filename in _document_names(entity.context_type):
        legacy_name = "preferences.md" if filename == "behavior.md" else filename
        path = directory / (filename if (directory / filename).exists() else legacy_name)
        if path.is_symlink() or not path.is_file():
            raise EntityContextError(f"Invalid {entity.context_type.value} document: {entity.name}/{filename}")
        try:
            content = path.read_text(encoding="utf-8")
            sections = people.populated_markdown_sections(content, filename)
        except (OSError, UnicodeError, people.PersonContextError) as error:
            raise EntityContextError(str(error)) from None
        documents.append(EntityDocument(filename, sections))
    return entity, tuple(documents)


def load_user_documents(reference, *, root=None):
    return load_entity_documents(load_user_context(reference, root=root), root=root)


def load_assistant_documents(reference, *, root=None):
    return load_entity_documents(load_assistant_context(reference, root=root), root=root)


def create_entity_context(entity, *, root=None):
    category = context_root(entity.context_type, root)
    if not os.path.lexists(category):
        try:
            category.mkdir(parents=True)
        except OSError as error:
            raise EntityContextError(
                f"Could not create {entity.context_type.value} context directory: {error}"
            ) from None
    if category.is_symlink() or not category.is_dir():
        raise EntityContextError(f"Invalid {entity.context_type.value} context directory: {category}")
    destination = category / entity.name
    legacy_destination = _legacy_root(entity.context_type, root) / entity.name
    if os.path.lexists(destination) or os.path.lexists(legacy_destination):
        raise EntityContextError(f"{entity.context_type.value.title()} context '{entity.name}' already exists.")
    files = render_entity_files(entity)
    try:
        destination.mkdir()
        for filename, content in files.items():
            people._write_document(destination / filename, content)
    except BaseException as error:
        people._rollback_person(destination, files)
        if isinstance(error, (KeyboardInterrupt, SystemExit)):
            raise
        raise EntityContextError(f"Could not create {entity.context_type.value} context '{entity.name}': {error}") from None
    return destination


def migrate_legacy_entity(name, context_type, *, root=None):
    context_type = ContextType(context_type)
    source = _legacy_root(context_type, root) / name
    destination = context_root(context_type, root) / name
    if destination.exists():
        canonical = _load_canonical(name, context_type, root)
        if not source.exists():
            return destination
        legacy = _load_legacy(name, context_type, root)
        if canonical.id != legacy.id:
            raise EntityContextError(
                f"Migration destination conflicts with legacy {context_type.value}: {name}"
            )
        return destination
    if source.is_symlink() or not source.is_dir():
        raise EntityContextError(f"Unknown legacy {context_type.value} context: {name}")
    category = context_root(context_type, root)
    category.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{name}.", dir=category))
    try:
        expected = set(_document_names(context_type)) | {"metadata.toml"}
        if context_type == ContextType.ASSISTANT:
            expected.discard("behavior.md")
            expected.add("preferences.md")
        for entry in source.iterdir():
            if entry.name not in expected:
                continue
            if entry.is_symlink() or not entry.is_file():
                raise EntityContextError(
                    f"Invalid legacy {context_type.value} migration file: {entry}"
                )
            target_name = (
                "behavior.md"
                if context_type == ContextType.ASSISTANT and entry.name == "preferences.md"
                else entry.name
            )
            shutil.copy2(entry, temporary / target_name)
        legacy_entity = _load_legacy(name, context_type, root)
        (temporary / "metadata.toml").write_text(
            render_metadata(legacy_entity), encoding="utf-8"
        )
        if context_type == ContextType.ASSISTANT:
            capabilities = temporary / "capabilities.toml"
            if not capabilities.exists():
                capabilities.write_text(SAFE_CAPABILITIES, encoding="utf-8")
        os.rename(temporary, destination)
        temporary = None
        shutil.rmtree(source)
    except BaseException:
        if temporary is not None:
            shutil.rmtree(temporary, ignore_errors=True)
        raise
    return destination
