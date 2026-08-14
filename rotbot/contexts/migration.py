from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import tempfile
import tomllib

from rotbot.contexts import documents, machines, matching


@dataclass(frozen=True)
class MigrationItem:
    name: str
    category: str
    source: Path
    destination: Path
    classification: str
    detail: str = ""


@dataclass(frozen=True)
class MigrationReport:
    moved: tuple[MigrationItem, ...]
    skipped: tuple[MigrationItem, ...]
    conflicted: tuple[MigrationItem, ...]


_SOURCE_LAYOUTS = (
    (Path("users"), "users"),
    (Path("assistants"), "assistants"),
    (Path("machines"), "machines"),
    (Path("projects"), "projects"),
    (Path("people/user"), "users"),
    (Path("people/assistant"), "assistants"),
    (Path("people/contact"), "contacts"),
)
_NAMESPACE_MAP = {"local": "private", "shareable": "general"}


class _UnsafeRecord(Exception):
    pass


def _item(name, category, source, destination, classification, detail=""):
    return MigrationItem(
        name=name,
        category=category,
        source=source,
        destination=destination,
        classification=classification,
        detail=detail,
    )


def _add_content(files, relative, content):
    if relative in files:
        raise _UnsafeRecord(f"multiple source files map to {relative}")
    files[relative] = content


def _add_file(files, relative, path):
    if path.is_symlink() or not path.is_file():
        raise _UnsafeRecord(f"unsupported non-regular file: {path}")
    try:
        _add_content(files, relative, path.read_bytes())
    except OSError as error:
        raise _UnsafeRecord(f"could not read {path}: {error}") from None


def _knowledge_content(path):
    try:
        content = path.read_bytes()
    except OSError as error:
        raise _UnsafeRecord(f"could not read {path}: {error}") from None
    if path.name == "relationship.md":
        return "relationships.md", content
    if path.suffix == ".md":
        return path.name, content
    if path.suffix == ".toml":
        try:
            text = content.decode("utf-8")
            tomllib.loads(text)
        except (UnicodeError, tomllib.TOMLDecodeError) as error:
            raise _UnsafeRecord(f"invalid legacy TOML knowledge {path}: {error}") from None
        rendered = f"# {path.stem.replace('_', ' ').title()}\n\n```toml\n{text.rstrip()}\n```\n"
        return f"{path.stem}.md", rendered.encode("utf-8")
    raise _UnsafeRecord(f"unsupported legacy knowledge document: {path}")


def _add_namespace(files, directories, source, legacy_namespace, category):
    root = source / legacy_namespace
    if not os.path.lexists(root):
        return
    if root.is_symlink() or not root.is_dir():
        raise _UnsafeRecord(f"invalid privacy namespace: {root}")
    namespace = _NAMESPACE_MAP[legacy_namespace]
    directories.add(Path(namespace))
    for path in root.iterdir():
        if path.is_dir() or path.is_symlink():
            raise _UnsafeRecord(f"nested privacy paths are not supported: {path}")
        if category == "projects" and path.name in {"match.toml", "match.md"}:
            if path.name == "match.md":
                try:
                    content = matching.render_match_toml(
                        matching.parse_legacy_match_document(
                            path.read_text(encoding="utf-8")
                        )
                    ).encode("utf-8")
                except (OSError, UnicodeError, matching.MatchError) as error:
                    raise _UnsafeRecord(f"invalid legacy project match: {error}") from None
                _add_content(files, Path("match.toml"), content)
            else:
                _add_file(files, Path("match.toml"), path)
            continue
        filename, content = _knowledge_content(path)
        _add_content(files, Path(namespace) / filename, content)


def _structural_metadata(metadata, category):
    if category != "machines":
        return None
    required = ("id", "name", "display_name")
    if any(not isinstance(metadata.get(key), str) for key in required):
        raise _UnsafeRecord("invalid machine metadata")
    return (
        'type = "machine"\n'
        f'id = {json.dumps(metadata["id"])}\n'
        f'name = {json.dumps(metadata["name"], ensure_ascii=False)}\n'
        f'display_name = {json.dumps(metadata["display_name"], ensure_ascii=False)}\n'
    ).encode("utf-8")


def _source_manifest(source, category=None):
    if source.is_symlink() or not source.is_dir():
        raise _UnsafeRecord(f"invalid source entity directory: {source}")
    metadata = source / "metadata.toml"
    try:
        metadata_content = metadata.read_bytes()
        metadata_document = tomllib.loads(metadata_content.decode("utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as error:
        raise _UnsafeRecord(f"invalid metadata {metadata}: {error}") from None
    files = {}
    directories = {Path("general"), Path("private")}
    structural = _structural_metadata(metadata_document, category)
    _add_content(files, Path("metadata.toml"), structural or metadata_content)
    context_type = {
        "users": "user", "assistants": "assistant", "machines": "machine",
        "projects": "project", "contacts": "contact"
    }.get(category, metadata_document.get("role", "contact"))
    name = metadata_document.get("name", source.name)
    display_name = metadata_document.get("display_name", name)
    identity_content = documents.render_identity(
        name, context_type, display_name
    ).encode("utf-8")
    if category != "users":
        _add_content(files, Path("identity.md"), identity_content)
    _add_content(
        files,
        Path("relationships.toml"),
        documents.render_relationships(
            metadata_document.get("related_projects", ())
        ).encode("utf-8")
    )
    if category == "machines":
        portable = {
            key: metadata_document[key]
            for key in (*machines.PORTABLE_SCALAR_FIELDS, "cpu", "memory", "gpus")
            if key in metadata_document
        }
        try:
            machine = machines.build_machine_context(
                name,
                display_name,
                portable,
                metadata_document.get("id")
            )
            machine_content = machines.render_machine_facts(machine).encode("utf-8")
        except machines.MachineContextError as error:
            raise _UnsafeRecord(f"invalid machine facts: {error}") from None
        _add_content(files, Path("machine.toml"), machine_content)
    for namespace in _NAMESPACE_MAP:
        _add_namespace(files, directories, source, namespace, category)
    try:
        entries = tuple(source.iterdir())
    except OSError as error:
        raise _UnsafeRecord(f"could not inspect {source}: {error}") from None
    for path in entries:
        if path.name == "metadata.toml" or path.name in _NAMESPACE_MAP:
            continue
        if path.is_symlink() or not path.is_file():
            raise _UnsafeRecord(f"unsupported source entry: {path}")
        if category == "assistants" and path.name == "capabilities.toml":
            _add_file(files, Path(path.name), path)
        elif category == "projects" and path.name in {"match.toml", "match.md"}:
            if path.name == "match.md":
                try:
                    content = matching.render_match_toml(
                        matching.parse_legacy_match_document(
                            path.read_text(encoding="utf-8")
                        )
                    ).encode("utf-8")
                except (OSError, UnicodeError, matching.MatchError) as error:
                    raise _UnsafeRecord(f"invalid legacy project match: {error}") from None
                _add_content(files, Path("match.toml"), content)
            else:
                _add_file(files, Path("match.toml"), path)
        else:
            filename, content = _knowledge_content(path)
            _add_content(files, Path("private") / filename, content)
    if category == "users":
        for namespace in ("general", "private"):
            files.setdefault(Path(namespace) / "identity.md", identity_content)
    return files, directories


def _write_manifest(root, files, directories):
    if os.name != "nt":
        os.chmod(root, 0o700)
    for relative in sorted(directories, key=lambda path: (len(path.parts), path.parts)):
        (root / relative).mkdir(mode=0o700)
    for relative, content in sorted(files.items(), key=lambda item: item[0].parts):
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if os.name != "nt":
            os.chmod(target, 0o600)


def _destination_matches(root, files, directories):
    if root.is_symlink() or not root.is_dir():
        return False
    actual_files = {}
    actual_directories = set()
    try:
        for current, directory_names, file_names in os.walk(root, followlinks=False):
            current_path = Path(current)
            relative_directory = current_path.relative_to(root)
            if relative_directory != Path("."):
                actual_directories.add(relative_directory)
            for directory_name in directory_names:
                if (current_path / directory_name).is_symlink():
                    return False
            for filename in file_names:
                path = current_path / filename
                if path.is_symlink() or not path.is_file():
                    return False
                actual_files[relative_directory / filename] = path.read_bytes()
    except OSError:
        return False
    return actual_files == files and actual_directories == directories


def _remove_verified_source(source, files, directories, destination, category):
    if not _destination_matches(destination, files, directories):
        raise _UnsafeRecord("destination verification failed before source deletion")
    current_files, current_directories = _source_manifest(source, category)
    if current_files != files or current_directories != directories:
        raise _UnsafeRecord("source changed during migration")
    try:
        shutil.rmtree(source)
    except OSError as error:
        raise _UnsafeRecord(f"could not delete verified source: {error}") from None


def _is_repository_context(source_root):
    repository_context = Path(__file__).absolute().parents[2] / "context"
    return source_root.absolute() == repository_context


def _contains_symlink(root, relative):
    current = root
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            return True
    return False


def migrate_contexts(
    source_root,
    destination_root,
    delete_source=False,
    builtin_assistant_names=("rot",),
    repository_source=None,
):
    source_root = Path(source_root)
    destination_root = Path(destination_root)
    if source_root.is_symlink() or not source_root.is_dir():
        raise ValueError(f"source_root must be a non-symlink directory: {source_root}")
    if destination_root.is_symlink():
        raise ValueError(f"destination_root must not be a symlink: {destination_root}")
    destination_root.mkdir(parents=True, mode=0o700, exist_ok=True)
    if os.name != "nt":
        os.chmod(destination_root, 0o700)
    if not destination_root.is_dir():
        raise ValueError(f"destination_root must be a directory: {destination_root}")

    moved = []
    skipped = []
    conflicted = []
    reported_builtins = set()
    repository_source = (
        _is_repository_context(source_root)
        if repository_source is None else bool(repository_source)
    )
    builtin_names = {name.casefold() for name in builtin_assistant_names}

    for source_relative, category in _SOURCE_LAYOUTS:
        source_category = source_root / source_relative
        if not os.path.lexists(source_category):
            continue
        if _contains_symlink(source_root, source_relative) or not source_category.is_dir():
            continue
        try:
            entries = sorted(source_category.iterdir(), key=lambda path: path.name)
        except OSError:
            continue
        for source in entries:
            if not source.is_dir() and not source.is_symlink():
                continue
            destination = destination_root / category / source.name
            if (
                repository_source
                and category == "assistants"
                and source.name.casefold() in builtin_names
            ):
                builtin_identity = (category, source.name.casefold())
                if builtin_identity in reported_builtins:
                    continue
                reported_builtins.add(builtin_identity)
                skipped.append(_item(
                    source.name, category, source, destination, "builtin",
                    "repository builtin assistant",
                ))
                continue
            if source.is_dir() and not os.path.lexists(source / "metadata.toml"):
                skipped.append(_item(
                    source.name, category, source, destination, "invalid",
                    "ignored directory without metadata.toml",
                ))
                continue
            try:
                files, directories = _source_manifest(source, category)
            except _UnsafeRecord as error:
                conflicted.append(_item(
                    source.name, category, source, destination, "conflict", str(error)
                ))
                continue

            if os.path.lexists(destination):
                if not _destination_matches(destination, files, directories):
                    conflicted.append(_item(
                        source.name, category, source, destination, "conflict",
                        "destination exists with different content",
                    ))
                    continue
                try:
                    if delete_source:
                        _remove_verified_source(source, files, directories, destination, category)
                except _UnsafeRecord as error:
                    conflicted.append(_item(
                        source.name, category, source, destination, "conflict", str(error)
                    ))
                    continue
                skipped.append(_item(
                    source.name, category, source, destination, "identical",
                    "identical destination already exists",
                ))
                continue

            category_root = destination.parent
            if category_root.is_symlink():
                conflicted.append(_item(
                    source.name, category, source, destination, "conflict",
                    "destination category is a symlink",
                ))
                continue
            try:
                category_root.mkdir(parents=True, mode=0o700, exist_ok=True)
                if os.name != "nt":
                    os.chmod(category_root, 0o700)
                staging = Path(tempfile.mkdtemp(prefix=f".{source.name}.", dir=category_root))
                try:
                    _write_manifest(staging, files, directories)
                    if not _destination_matches(staging, files, directories):
                        raise _UnsafeRecord("staged copy verification failed")
                    try:
                        os.rename(staging, destination)
                        staging = None
                    except FileExistsError:
                        raise _UnsafeRecord("destination appeared during migration") from None
                finally:
                    if staging is not None:
                        shutil.rmtree(staging, ignore_errors=True)
                if delete_source:
                    _remove_verified_source(source, files, directories, destination, category)
            except (OSError, _UnsafeRecord) as error:
                conflicted.append(_item(
                    source.name, category, source, destination, "conflict", str(error)
                ))
                continue
            moved.append(_item(source.name, category, source, destination, "moved"))

    return MigrationReport(tuple(moved), tuple(skipped), tuple(conflicted))
