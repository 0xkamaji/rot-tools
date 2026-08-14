import json
import os
from pathlib import Path
import shutil
import tempfile
import tomllib
import uuid


KNOWLEDGE_NAMESPACES = ("general", "private")
LEGACY_NAMESPACES = ("shareable", "local")


class ContextDocumentError(Exception):
    pass


def recover_interrupted_migration(directory):
    directory = Path(directory)
    if os.path.lexists(directory):
        return False
    prefix = f".{directory.name}.legacy-"
    try:
        backups = tuple(
            path for path in directory.parent.iterdir()
            if path.name.startswith(prefix)
        )
    except OSError as error:
        raise ContextDocumentError(f"Could not inspect migration backups: {error}") from None
    if not backups:
        return False
    if len(backups) != 1 or backups[0].is_symlink() or not backups[0].is_dir():
        raise ContextDocumentError(f"Ambiguous interrupted migration for: {directory}")
    try:
        os.rename(backups[0], directory)
    except OSError as error:
        raise ContextDocumentError(f"Could not recover interrupted migration: {error}") from None
    return True


def recover_interrupted_migrations(category):
    category = Path(category)
    if category.is_symlink() or not category.is_dir():
        return
    try:
        names = {
            path.name[1:].split(".legacy-", 1)[0]
            for path in category.iterdir()
            if path.name.startswith(".") and ".legacy-" in path.name
        }
    except OSError as error:
        raise ContextDocumentError(f"Could not inspect migration backups: {error}") from None
    for name in names:
        recover_interrupted_migration(category / name)


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
        line = raw_line
        output = ""
        while line:
            if in_comment:
                end = line.find("-->")
                if end < 0:
                    line = ""
                    break
                line = line[end + 3:]
                in_comment = False
                continue
            start = line.find("<!--")
            if start < 0:
                output += line
                break
            output += line[:start]
            line = line[start + 4:]
            in_comment = True
        stripped = output.strip()
        if fence is not None:
            body.append(output)
            if stripped.startswith(fence):
                fence = None
            continue
        if stripped.startswith("```") or stripped.startswith("~~~"):
            fence = stripped[:3]
            body.append(output)
            continue
        if output.startswith("## "):
            finish_section()
            heading = output[3:].strip()
            body = []
            continue
        if heading is None and output.startswith("# ") and not "".join(body).strip():
            continue
        body.append(output)
    if in_comment:
        raise ContextDocumentError(f"Unterminated Markdown comment: {filename}")
    if fence is not None:
        raise ContextDocumentError(f"Unterminated Markdown fence: {filename}")
    finish_section()
    return tuple(sections)


def _read_regular(path, label):
    if path.is_symlink() or not path.is_file():
        raise ContextDocumentError(f"Invalid {label}: {path}")
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise ContextDocumentError(f"Could not read {label}: {error}") from None


def render_identity(name, context_type, display_name=None):
    title = display_name or name
    descriptions = {
        "assistant": "Personal context-aware assistant.",
        "user": "Person who uses RotBot.",
        "contact": "Person known to the RotBot user.",
        "machine": "Machine known to RotBot.",
        "project": "Project known to RotBot."
    }
    return f"# {title}\n\n{descriptions.get(context_type, 'Context entity.')}\n"


def render_relationships(related_projects=()):
    blocks = []
    for project in related_projects:
        blocks.append(
            "[[relationship]]\n"
            f"target = {json.dumps(f'projects/{project}')}\n"
            'type = "related"'
        )
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def load_relationships(directory):
    path = Path(directory) / "relationships.toml"
    content = _read_regular(path, "relationships document")
    try:
        document = tomllib.loads(content)
    except tomllib.TOMLDecodeError as error:
        raise ContextDocumentError(f"Invalid relationships TOML: {error}") from None
    if set(document) - {"relationship"}:
        raise ContextDocumentError("relationships.toml contains unsupported fields.")
    values = document.get("relationship", [])
    if not isinstance(values, list):
        raise ContextDocumentError("relationships.toml relationship must be an array.")
    relationships = []
    for value in values:
        if (
            not isinstance(value, dict)
            or set(value) != {"target", "type"}
            or not isinstance(value.get("target"), str)
            or not isinstance(value.get("type"), str)
            or not value["target"]
            or not value["type"]
            or "\0" in value["target"]
            or "\0" in value["type"]
        ):
            raise ContextDocumentError("Invalid relationship entry.")
        relationships.append((value["target"], value["type"]))
    if len(set(relationships)) != len(relationships):
        raise ContextDocumentError("Duplicate relationship entry.")
    return tuple(relationships)


def _write_private(path, content):
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    with path.open("x", encoding="utf-8") as destination:
        destination.write(content)
        destination.flush()
        os.fsync(destination.fileno())
    if os.name != "nt":
        os.chmod(path, 0o600)


def _legacy_metadata(directory):
    path = directory / "metadata.toml"
    if not path.exists():
        return {}
    try:
        return tomllib.loads(_read_regular(path, "context metadata"))
    except tomllib.TOMLDecodeError as error:
        raise ContextDocumentError(f"Invalid context metadata: {error}") from None


def _copy_knowledge_file(path, destination, disclosure):
    if path.name == ".gitkeep" and path.is_file() and not path.is_symlink():
        return
    content = _read_regular(path, "legacy context document")
    if path.name in {"match.toml", "match.md"}:
        return
    if path.suffix == ".toml":
        try:
            tomllib.loads(content)
        except tomllib.TOMLDecodeError as error:
            raise ContextDocumentError(
                f"Invalid legacy TOML knowledge {path.name}: {error}"
            ) from None
        filename = f"{path.stem}.md"
        title = path.stem.replace("_", " ").replace("-", " ").title()
        content = f"# {title}\n\n```toml\n{content.rstrip()}\n```\n"
    elif path.name == "relationship.md":
        filename = "relationships.md"
    elif path.suffix == ".md":
        filename = path.name
    else:
        raise ContextDocumentError(
            f"Unsupported legacy knowledge document: {path.name}"
        )
    target = destination / disclosure / filename
    if target.exists():
        if target.read_text(encoding="utf-8") != content:
            raise ContextDocumentError(
                f"Conflicting migrated knowledge document: {disclosure}/{filename}"
            )
        return
    _write_private(target, content)


def _copy_knowledge(source, destination, disclosure):
    if not os.path.lexists(source):
        return
    if source.is_symlink() or not source.is_dir():
        raise ContextDocumentError(f"Invalid legacy knowledge directory: {source}")
    for path in sorted(source.iterdir(), key=lambda item: item.name):
        _copy_knowledge_file(path, destination, disclosure)


def _render_legacy_machine_facts(metadata):
    fields = (
        "device_type", "operating_system", "operating_system_version", "architecture"
    )
    lines = [
        f"{field} = {json.dumps(metadata[field], ensure_ascii=False)}"
        for field in fields if field in metadata
    ]
    for table_name in ("cpu", "memory"):
        table = metadata.get(table_name)
        if not isinstance(table, dict) or not table:
            continue
        lines.extend(("", f"[{table_name}]"))
        lines.extend(
            f"{key} = {json.dumps(value, ensure_ascii=False)}"
            for key, value in table.items()
        )
    for gpu in metadata.get("gpus", ()):
        if not isinstance(gpu, dict) or not gpu:
            continue
        lines.extend(("", "[[gpus]]"))
        lines.extend(
            f"{key} = {json.dumps(value, ensure_ascii=False)}"
            for key, value in gpu.items()
        )
    content = "\n".join(lines) + ("\n" if lines else "")
    try:
        tomllib.loads(content)
    except tomllib.TOMLDecodeError as error:
        raise ContextDocumentError(f"Invalid migrated machine facts: {error}") from None
    return content


def _migrated_match_toml(path):
    from rotbot.contexts.matching import (
        MatchError, parse_legacy_match_document, render_match_toml
    )

    content = _read_regular(path, "legacy project match document")
    try:
        return render_match_toml(parse_legacy_match_document(content))
    except MatchError as error:
        raise ContextDocumentError(str(error)) from None


def migrate_legacy_layout(directory, context_type=None):
    directory = Path(directory)
    legacy_present = any(os.path.lexists(directory / name) for name in LEGACY_NAMESPACES)
    if not legacy_present:
        return False
    if directory.is_symlink() or not directory.is_dir():
        raise ContextDocumentError(f"Invalid context directory: {directory}")
    metadata = _legacy_metadata(directory)
    inferred = context_type or metadata.get("type") or directory.parent.name.rstrip("s")
    if inferred == "person":
        inferred = metadata.get("role", "contact")
    name = metadata.get("name", directory.name)
    display_name = metadata.get("display_name", name)
    stage = Path(tempfile.mkdtemp(prefix=f".{directory.name}.schema-", dir=directory.parent))
    backup = directory.with_name(f".{directory.name}.legacy-{uuid.uuid4().hex}")
    replaced = False
    try:
        if os.name != "nt":
            os.chmod(stage, 0o700)
        for namespace in KNOWLEDGE_NAMESPACES:
            (stage / namespace).mkdir(mode=0o700)
        for path in sorted(directory.iterdir(), key=lambda item: item.name):
            if path.name in LEGACY_NAMESPACES:
                continue
            if path.is_symlink():
                raise ContextDocumentError(f"Invalid legacy context entry: {path}")
            if path.is_dir():
                if path.name not in KNOWLEDGE_NAMESPACES:
                    raise ContextDocumentError(f"Unsupported context directory: {path}")
                _copy_knowledge(path, stage, path.name)
                continue
            if path.name.endswith(".md"):
                _copy_knowledge_file(path, stage, "private")
                continue
            operational = {
                "metadata.toml", "capabilities.toml", "match.toml", "match.md",
                "machine.toml", "relationships.toml"
            }
            if path.name not in operational:
                _copy_knowledge_file(path, stage, "private")
                continue
            shutil.copyfile(path, stage / path.name)
            if os.name != "nt":
                os.chmod(stage / path.name, 0o600)

        identity = render_identity(name, inferred, display_name)
        if inferred != "user":
            _write_private(stage / "identity.md", identity)
        _write_private(
            stage / "relationships.toml",
            render_relationships(metadata.get("related_projects", ()))
        )
        if inferred == "machine" and (stage / "metadata.toml").is_file():
            _write_private(
                stage / "machine.toml", _render_legacy_machine_facts(metadata)
            )
            structural = (
                'type = "machine"\n'
                f"id = {json.dumps(metadata.get('id'))}\n"
                f"name = {json.dumps(name)}\n"
                f"display_name = {json.dumps(display_name)}\n"
            )
            (stage / "metadata.toml").write_text(structural, encoding="utf-8")
            if os.name != "nt":
                os.chmod(stage / "metadata.toml", 0o600)
        _copy_knowledge(directory / "shareable", stage, "general")
        _copy_knowledge(directory / "local", stage, "private")
        if inferred == "user":
            for namespace in KNOWLEDGE_NAMESPACES:
                path = stage / namespace / "identity.md"
                if not path.exists():
                    _write_private(path, identity)

        for filename in ("match.toml", "match.md"):
            candidates = (
                directory / "local" / filename,
                directory / "shareable" / filename,
                directory / filename
            )
            source = next((path for path in candidates if path.is_file()), None)
            if source is not None and not (stage / "match.toml").exists():
                content = (
                    _migrated_match_toml(source)
                    if source.name == "match.md"
                    else source.read_text(encoding="utf-8")
                )
                _write_private(stage / "match.toml", content)
                break

        os.rename(directory, backup)
        try:
            os.rename(stage, directory)
            replaced = True
        except BaseException:
            os.rename(backup, directory)
            raise
        shutil.rmtree(backup)
        return True
    except ContextDocumentError:
        raise
    except (OSError, UnicodeError) as error:
        raise ContextDocumentError(f"Could not migrate context layout: {error}") from None
    finally:
        if stage.exists():
            shutil.rmtree(stage, ignore_errors=True)
        if replaced and backup.exists():
            shutil.rmtree(backup, ignore_errors=True)


def ensure_structure(directory, context_type=None):
    directory = Path(directory)
    recover_interrupted_migration(directory)
    migrate_legacy_layout(directory, context_type)
    if directory.is_symlink() or not directory.is_dir():
        raise ContextDocumentError(f"Invalid context directory: {directory}")
    inferred = context_type.value if hasattr(context_type, "value") else context_type
    if inferred is None:
        inferred = _legacy_metadata(directory).get("type")
    required = ("relationships.toml",) if inferred == "user" else (
        "identity.md", "relationships.toml"
    )
    if inferred == "user" and os.path.lexists(directory / "identity.md"):
        raise ContextDocumentError(
            f"User identity must be namespaced: {directory / 'identity.md'}"
        )
    for filename in required:
        path = directory / filename
        if path.is_symlink() or not path.is_file():
            raise ContextDocumentError(f"Invalid context structural document: {path}")
    for namespace in KNOWLEDGE_NAMESPACES:
        root = directory / namespace
        if root.is_symlink() or not root.is_dir():
            raise ContextDocumentError(f"Invalid knowledge namespace: {root}")
        if inferred == "user":
            identity = root / "identity.md"
            if identity.is_symlink() or not identity.is_file():
                raise ContextDocumentError(
                    f"Invalid user identity document: {identity}"
                )
    return directory


def namespace_files(directory, namespace):
    if namespace not in KNOWLEDGE_NAMESPACES:
        raise ContextDocumentError(f"Unknown knowledge namespace: {namespace}")
    root = ensure_structure(directory) / namespace
    files = []
    for path in sorted(root.iterdir(), key=lambda item: item.name):
        if path.name == ".gitkeep" and path.is_file() and not path.is_symlink():
            continue
        if path.is_symlink() or not path.is_file() or path.suffix != ".md":
            raise ContextDocumentError(f"Invalid knowledge document: {path}")
        files.append(path)
    return tuple(files)


def semantic_files(directory, view):
    root = ensure_structure(directory)
    if view == "egress":
        namespaces = ("general",)
    elif view == "full":
        namespaces = KNOWLEDGE_NAMESPACES
    else:
        raise ContextDocumentError(f"Unknown context view: {view}")
    files = [root / "identity.md"] if (root / "identity.md").is_file() else []
    for namespace in namespaces:
        files.extend(namespace_files(root, namespace))
    return tuple(files)


def privacy_inventory(directory):
    root = ensure_structure(directory)
    return {
        namespace: tuple(path.name for path in namespace_files(root, namespace))
        for namespace in KNOWLEDGE_NAMESPACES
    }
