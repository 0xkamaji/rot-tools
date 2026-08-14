from dataclasses import dataclass
from enum import Enum
import json
import os
from pathlib import Path
import shutil
import tempfile
import tomllib
import uuid

from rotbot.contexts import documents, loader, people
from rotbot.contexts.paths import builtin_assistants_root
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


def _builtin_directory(reference, context_type):
    if ContextType(context_type) != ContextType.ASSISTANT:
        return None
    category = builtin_assistants_root()
    if not category.is_dir() or category.is_symlink():
        return None
    direct = category / str(reference)
    if direct.is_dir() and not direct.is_symlink():
        return direct
    for entry in category.iterdir():
        if entry.is_symlink() or not entry.is_dir():
            continue
        try:
            if _read_metadata(entry, ContextType.ASSISTANT).get("id") == reference:
                return entry
        except EntityContextError:
            continue
    return None


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
        or len(display_name) > 200
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
    if not isinstance(entity, (UserContext, AssistantContext)):
        raise EntityContextError(f"Unsupported entity context: {entity!r}")
    files = {
        "metadata.toml": render_metadata(entity),
        "identity.md": documents.render_identity(
            entity.name, entity.context_type.value, entity.display_name
        ),
        "relationships.toml": documents.render_relationships(entity.related_projects)
    }
    if isinstance(entity, AssistantContext):
        files["capabilities.toml"] = SAFE_CAPABILITIES
    return files


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
        documents.recover_interrupted_migrations(category)
    except documents.ContextDocumentError as error:
        raise EntityContextError(str(error)) from None
    try:
        uuid.UUID(str(reference))
        reference_is_id = True
    except (ValueError, TypeError, AttributeError):
        reference_is_id = False
    direct = category / str(reference)
    try:
        documents.recover_interrupted_migration(direct)
    except documents.ContextDocumentError as error:
        raise EntityContextError(str(error)) from None
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
    if directory is None and root is None:
        directory = _builtin_directory(reference, context_type)
    if directory is None:
        return None
    try:
        documents.ensure_structure(directory, context_type.value)
    except documents.ContextDocumentError as error:
        raise EntityContextError(str(error)) from None
    metadata = _read_metadata(directory, context_type)
    builder = build_user_context if context_type == ContextType.USER else build_assistant_context
    entity = builder(
        metadata.get("name"), metadata.get("display_name"),
        metadata.get("related_projects", []), metadata.get("id")
    )
    if context_type == ContextType.ASSISTANT:
        capabilities = directory / "capabilities.toml"
        if capabilities.is_symlink() or not capabilities.is_file():
            raise EntityContextError(
                f"Invalid assistant capabilities: {entity.name}/capabilities.toml"
            )
    return entity


def load_user_context(reference, *, root=None):
    entity = _load_canonical(reference, ContextType.USER, root)
    if entity is None:
        raise EntityContextError(f"Unknown user context reference: {reference}")
    return entity


def load_assistant_context(reference, *, root=None):
    entity = _load_canonical(reference, ContextType.ASSISTANT, root)
    if entity is None:
        raise EntityContextError(f"Unknown assistant context reference: {reference}")
    return entity


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
    if root is None and ContextType(context_type) == ContextType.ASSISTANT:
        category = builtin_assistants_root()
        if category.is_dir() and not category.is_symlink():
            for entry in category.iterdir():
                try:
                    entity = _load_canonical(entry.name, context_type, category.parent)
                except EntityContextError:
                    continue
                by_id.setdefault(entity.id, entity)
    return tuple(sorted(by_id.values(), key=lambda item: item.name))


def list_user_contexts(*, root=None):
    return _list(ContextType.USER, root)


def list_assistant_contexts(*, root=None):
    return _list(ContextType.ASSISTANT, root)


def load_entity_documents(entity, *, root=None, view="full"):
    directory = entity_directory(entity, root)
    if directory.exists():
        metadata = _read_metadata(directory, entity.context_type)
        if metadata.get("id") != entity.id:
            raise EntityContextError(
                f"Canonical {entity.context_type.value} context conflicts with reference: "
                f"{entity.name}"
            )
    else:
        directory = (
            _builtin_directory(entity.name, entity.context_type)
            if root is None else None
        )
        if directory is None:
            raise EntityContextError(
                f"Unknown {entity.context_type.value} context: {entity.name}"
            )

    def load_source(source):
        loaded = []
        paths = documents.semantic_files(source, view)
        for path in paths:
            filename = path.name
            content = path.read_text(encoding="utf-8")
            sections = documents.populated_markdown_sections(content, filename)
            if sections:
                loaded.append(EntityDocument(filename, sections))
        return loaded

    try:
        if entity.context_type == ContextType.ASSISTANT:
            return entity, tuple(load_source(directory))

        paths = list(documents.semantic_files(directory, view))
    except (OSError, UnicodeError, documents.ContextDocumentError) as error:
        raise EntityContextError(str(error)) from None
    loaded_documents = []
    for path in paths:
        filename = path.name
        try:
            content = path.read_text(encoding="utf-8")
            sections = documents.populated_markdown_sections(content, filename)
        except (OSError, UnicodeError, documents.ContextDocumentError) as error:
            raise EntityContextError(str(error)) from None
        loaded_documents.append(EntityDocument(filename, sections))
    return entity, tuple(loaded_documents)


def load_user_documents(reference, *, root=None, view="full"):
    return load_entity_documents(load_user_context(reference, root=root), root=root, view=view)


def load_assistant_documents(reference, *, root=None, view="full"):
    return load_entity_documents(load_assistant_context(reference, root=root), root=root, view=view)


def create_entity_context(entity, *, root=None):
    category = context_root(entity.context_type, root)
    if not os.path.lexists(category):
        try:
            category.mkdir(parents=True, mode=0o700)
        except OSError as error:
            raise EntityContextError(
                f"Could not create {entity.context_type.value} context directory: {error}"
            ) from None
    if category.is_symlink() or not category.is_dir():
        raise EntityContextError(f"Invalid {entity.context_type.value} context directory: {category}")
    destination = category / entity.name
    if os.path.lexists(destination):
        raise EntityContextError(f"{entity.context_type.value.title()} context '{entity.name}' already exists.")
    files = render_entity_files(entity)
    try:
        destination.mkdir(mode=0o700)
        people._write_document(destination / "metadata.toml", files.pop("metadata.toml"))
        capabilities = files.pop("capabilities.toml", None)
        if capabilities is not None:
            people._write_document(destination / "capabilities.toml", capabilities)
        people._write_document(destination / "identity.md", files.pop("identity.md"))
        people._write_document(
            destination / "relationships.toml", files.pop("relationships.toml")
        )
        (destination / "general").mkdir(mode=0o700)
        (destination / "private").mkdir(mode=0o700)
    except BaseException as error:
        shutil.rmtree(destination, ignore_errors=True)
        if isinstance(error, (KeyboardInterrupt, SystemExit)):
            raise
        raise EntityContextError(f"Could not create {entity.context_type.value} context '{entity.name}': {error}") from None
    return destination


def materialize_builtin_assistant(reference, *, root=None):
    builtin = _builtin_directory(reference, ContextType.ASSISTANT)
    if builtin is None:
        raise EntityContextError(f"Unknown built-in assistant context: {reference}")
    metadata = _read_metadata(builtin, ContextType.ASSISTANT)
    assistant = build_assistant_context(
        metadata.get("name"), metadata.get("display_name"),
        metadata.get("related_projects", []), metadata.get("id")
    )
    capabilities = builtin / "capabilities.toml"
    if capabilities.is_symlink() or not capabilities.is_file():
        raise EntityContextError(
            f"Invalid built-in assistant capabilities: {assistant.name}"
        )
    try:
        capability_text = capabilities.read_text(encoding="utf-8")
        tomllib.loads(capability_text)
        documents.ensure_structure(builtin, "assistant")
        identity_text = (builtin / "identity.md").read_text(encoding="utf-8")
        relationships_text = (builtin / "relationships.toml").read_text(encoding="utf-8")
        seeded = {
            namespace: {
                path.name: path.read_text(encoding="utf-8")
                for path in documents.namespace_files(builtin, namespace)
            }
            for namespace in documents.KNOWLEDGE_NAMESPACES
        }
    except (OSError, UnicodeError, tomllib.TOMLDecodeError, documents.ContextDocumentError) as error:
        raise EntityContextError(
            f"Could not read built-in assistant '{assistant.name}': {error}"
        ) from None

    category = context_root(ContextType.ASSISTANT, root)
    destination = category / assistant.name
    if os.path.lexists(destination):
        if destination.is_symlink() or not destination.is_dir():
            raise EntityContextError(
                f"Conflicting local assistant context: {assistant.name}"
            )
        local_metadata = _read_metadata(destination, ContextType.ASSISTANT)
        if local_metadata.get("id") != assistant.id:
            raise EntityContextError(
                f"Local assistant '{assistant.name}' conflicts with built-in ID."
            )
        local_assistant = _load_canonical(
            assistant.name, ContextType.ASSISTANT, root
        )
        if local_assistant is None:
            raise EntityContextError(
                f"Invalid local assistant context: {assistant.name}"
            )
        return local_assistant, destination

    temporary = None
    try:
        category.mkdir(parents=True, mode=0o700, exist_ok=True)
        if category.is_symlink() or not category.is_dir():
            raise EntityContextError(f"Invalid assistant context directory: {category}")
        if os.name != "nt":
            os.chmod(category, 0o700)
        temporary = Path(tempfile.mkdtemp(prefix=f".{assistant.name}.", dir=category))
        if os.name != "nt":
            os.chmod(temporary, 0o700)
        people._write_document(
            temporary / "metadata.toml", builtin.joinpath("metadata.toml").read_text(
                encoding="utf-8"
            )
        )
        people._write_document(temporary / "capabilities.toml", capability_text)
        people._write_document(temporary / "identity.md", identity_text)
        people._write_document(temporary / "relationships.toml", relationships_text)
        for namespace in documents.KNOWLEDGE_NAMESPACES:
            target = temporary / namespace
            target.mkdir(mode=0o700)
            for filename, content in seeded[namespace].items():
                people._write_document(target / filename, content)
        os.rename(temporary, destination)
        temporary = None
        loaded = _load_canonical(assistant.id, ContextType.ASSISTANT, root)
        if loaded != assistant:
            raise EntityContextError(
                f"Materialized assistant validation failed: {assistant.name}"
            )
        return assistant, destination
    except FileExistsError:
        if os.path.lexists(destination):
            local_metadata = _read_metadata(destination, ContextType.ASSISTANT)
            if local_metadata.get("id") == assistant.id:
                return assistant, destination
        raise EntityContextError(
            f"Conflicting local assistant context: {assistant.name}"
        ) from None
    except EntityContextError:
        raise
    except (OSError, UnicodeError) as error:
        raise EntityContextError(
            f"Could not materialize built-in assistant '{assistant.name}': {error}"
        ) from None
    finally:
        if temporary is not None:
            shutil.rmtree(temporary, ignore_errors=True)
