import json
import os
from pathlib import Path
import shutil
import stat
import tomllib
from typing import NamedTuple

from rotbot.contexts import documents, loader
from rotbot.contexts.config import ConfigError, config_path, legacy_config_path
from rotbot.contexts.identifiers import (
    ContextIdentifierError,
    legacy_context_id,
    new_context_id,
    validate_context_id
)


PORTABLE_FILENAMES = ("metadata.toml", "identity.md", "software.toml", "learned.md")
IDENTITY_TEMPLATE = (
    "# Identity\n\n"
    "<!-- A general overview of this machine, its purpose, and its place in the larger computing environment. -->\n\n"
    "## Purpose\n\n"
    "<!-- What this machine is primarily used for. -->\n\n"
    "## Environment\n\n"
    "<!-- How this machine relates to other devices, projects, people, and workflows. -->\n\n"
    "## Important Context\n\n"
    "<!-- Useful information about the machine that does not fit naturally into structured metadata. -->\n"
)
PORTABLE_SCALAR_FIELDS = (
    "device_type",
    "operating_system",
    "operating_system_version",
    "architecture"
)
LOCAL_SECTIONS = ("connection", "network", "services", "users")


class MachineContextError(Exception):
    pass


class MachineContext(NamedTuple):
    name: str
    display_name: str
    portable_facts: dict
    id: str | None = None


class MachineDocument(NamedTuple):
    filename: str
    content: str


def _validate_text(value, label):
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 1000
        or any(ord(character) < 32 for character in value)
    ):
        raise MachineContextError(f"Invalid machine {label}.")
    return value


def _validate_identifier(value, label):
    try:
        return loader.validate_context_name(value)
    except loader.ContextError:
        raise MachineContextError(f"Invalid machine {label}: {value}") from None


def _validate_positive_number(value, label, *, integer=False):
    valid_type = isinstance(value, int) and not isinstance(value, bool)
    if not integer:
        valid_type = valid_type or isinstance(value, float)
    if not valid_type or value <= 0:
        raise MachineContextError(f"Invalid machine {label}.")
    return value


def _validate_optional_table(value, label, fields):
    if value is None:
        return None
    if not isinstance(value, dict) or not value:
        raise MachineContextError(f"Invalid machine {label}.")
    unknown = set(value) - set(fields)
    if unknown:
        raise MachineContextError(f"Unsupported machine {label} field: {sorted(unknown)[0]}")
    result = {}
    for name, validator in fields.items():
        if name in value:
            result[name] = validator(value[name], f"{label} {name}")
    if not result:
        raise MachineContextError(f"Invalid machine {label}.")
    return result


def validate_portable_facts(portable_facts=None):
    if portable_facts is None:
        return {}
    if not isinstance(portable_facts, dict):
        raise MachineContextError("Invalid portable machine facts.")
    allowed = set(PORTABLE_SCALAR_FIELDS) | {"cpu", "memory", "gpus"}
    unknown = set(portable_facts) - allowed
    if unknown:
        raise MachineContextError(f"Unsupported portable machine fact: {sorted(unknown)[0]}")
    facts = {}
    for field in PORTABLE_SCALAR_FIELDS:
        if field in portable_facts:
            facts[field] = _validate_text(portable_facts[field], field.replace("_", " "))
    cpu = _validate_optional_table(
        portable_facts.get("cpu"),
        "CPU",
        {
            "model": _validate_text,
            "physical_cores": lambda value, label: _validate_positive_number(
                value, label, integer=True
            ),
            "logical_cores": lambda value, label: _validate_positive_number(
                value, label, integer=True
            )
        }
    )
    if cpu is not None:
        facts["cpu"] = cpu
    memory = _validate_optional_table(
        portable_facts.get("memory"),
        "memory",
        {
            "total_gb": _validate_positive_number
        }
    )
    if memory is not None:
        facts["memory"] = memory
    if "gpus" in portable_facts:
        gpus = portable_facts["gpus"]
        if not isinstance(gpus, list):
            raise MachineContextError("Invalid machine GPUs.")
        normalized = []
        for gpu in gpus:
            parsed = _validate_optional_table(
                gpu,
                "GPU",
                {"model": _validate_text, "vram_gb": _validate_positive_number}
            )
            if parsed is None or "model" not in parsed:
                raise MachineContextError("Invalid machine GPU.")
            normalized.append(parsed)
        if normalized:
            facts["gpus"] = normalized
    return facts


def build_machine_context(name, display_name=None, portable_facts=None, context_id=None):
    name = _validate_identifier(name, "name")
    display_name = name if display_name is None else display_name
    try:
        normalized_id = validate_context_id(context_id or new_context_id())
    except ContextIdentifierError as error:
        raise MachineContextError(str(error)) from None
    return MachineContext(
        name,
        _validate_text(display_name, "display name"),
        validate_portable_facts(portable_facts),
        normalized_id
    )


def _toml_value(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return json.dumps(value, ensure_ascii=False)


def render_machine_metadata(machine):
    lines = [
        'type = "machine"',
        f"id = {_toml_value(machine.id)}",
        f"name = {_toml_value(machine.name)}",
        f"display_name = {_toml_value(machine.display_name)}"
    ]
    facts = machine.portable_facts
    for field in PORTABLE_SCALAR_FIELDS:
        if field in facts:
            lines.append(f"{field} = {_toml_value(facts[field])}")
    for table_name in ("cpu", "memory"):
        if table_name not in facts:
            continue
        lines.extend(("", f"[{table_name}]"))
        for key, value in facts[table_name].items():
            lines.append(f"{key} = {_toml_value(value)}")
    for gpu in facts.get("gpus", ()):
        lines.extend(("", "[[gpus]]"))
        for key, value in gpu.items():
            lines.append(f"{key} = {_toml_value(value)}")
    metadata = "\n".join(lines) + "\n"
    try:
        tomllib.loads(metadata)
    except tomllib.TOMLDecodeError as error:
        raise MachineContextError(f"Could not render valid machine metadata: {error}") from None
    return metadata


def render_machine_files(machine):
    return {
        "metadata.toml": render_machine_metadata(machine),
        "identity.md": IDENTITY_TEMPLATE,
        "software.toml": "",
        "learned.md": "# Learned\n"
    }


def _machines_root(machines_root=None, create=False):
    root = Path(machines_root) if machines_root is not None else loader.CONTEXT_ROOT / "machines"
    if create and not os.path.lexists(root):
        root.mkdir(parents=True, mode=0o700)
    if root.is_symlink() or not root.is_dir():
        raise MachineContextError(f"Invalid machine context directory: {root}")
    return root


def machine_context_directory(name, *, machines_root=None):
    name = _validate_identifier(name, "name")
    root = _machines_root(machines_root)
    directory = root / name
    if directory.is_symlink() or not directory.is_dir():
        raise MachineContextError(f"Unknown or invalid machine context: {name}")
    return directory


def _read_portable_files(name, directory):
    loaded = []
    metadata_path = directory / "metadata.toml"
    paths = [metadata_path]
    try:
        paths.extend(documents.semantic_files(
            directory, "full", {"identity.md", "software.toml", "learned.md"}
        ))
    except documents.ContextDocumentError as error:
        raise MachineContextError(str(error)) from None
    for path in paths:
        filename = path.name
        if path.is_symlink() or not path.is_file():
            raise MachineContextError(f"Invalid machine document: {name}/{filename}")
        try:
            content = path.read_text(encoding="utf-8")
            if filename.endswith(".toml"):
                tomllib.loads(content)
        except (OSError, UnicodeError, tomllib.TOMLDecodeError) as error:
            raise MachineContextError(
                f"Could not load machine document '{name}/{filename}': {error}"
            ) from None
        loaded.append(MachineDocument(filename, content))
    return tuple(loaded)


def load_machine_context(name, *, machines_root=None):
    directory = machine_context_directory(name, machines_root=machines_root)
    metadata = tomllib.loads((directory / "metadata.toml").read_text(encoding="utf-8"))
    if (
        metadata.get("type") != "machine"
        or metadata.get("name") != name
        or "display_name" not in metadata
    ):
        raise MachineContextError(f"Invalid machine metadata: {name}")
    facts = {
        key: metadata[key]
        for key in (*PORTABLE_SCALAR_FIELDS, "cpu", "memory", "gpus")
        if key in metadata
    }
    return build_machine_context(
        name,
        metadata.get("display_name"),
        facts,
        metadata.get("id") or legacy_context_id("machine", name)
    )


def load_machine_files(name, *, machines_root=None, view="full"):
    directory = machine_context_directory(name, machines_root=machines_root)
    machine = load_machine_context(name, machines_root=machines_root)
    loaded = [MachineDocument("metadata.toml", (directory / "metadata.toml").read_text(encoding="utf-8"))]
    try:
        paths = documents.semantic_files(
            directory, view, {"identity.md", "software.toml", "learned.md"},
            include_legacy_local=view == "full"
        )
    except documents.ContextDocumentError as error:
        raise MachineContextError(str(error)) from None
    loaded.extend(MachineDocument(path.name, path.read_text(encoding="utf-8")) for path in paths)
    return machine, tuple(loaded)


def list_machine_contexts(*, machines_root=None):
    root = Path(machines_root) if machines_root is not None else loader.CONTEXT_ROOT / "machines"
    if not root.exists():
        return ()
    root = _machines_root(root)
    try:
        entries = tuple(root.iterdir())
    except OSError as error:
        raise MachineContextError(f"Could not list machine contexts: {error}") from None
    contexts = []
    for entry in entries:
        try:
            contexts.append(load_machine_context(entry.name, machines_root=root))
        except MachineContextError:
            continue
    return tuple(sorted(contexts, key=lambda machine: machine.name))


def load_machine_context_reference(reference, *, machines_root=None):
    matches = tuple(
        machine
        for machine in list_machine_contexts(machines_root=machines_root)
        if reference in {machine.id, machine.name}
    )
    if not matches:
        raise MachineContextError(f"Unknown machine context reference: {reference}")
    if len(matches) > 1:
        raise MachineContextError(f"Ambiguous machine context reference: {reference}")
    return matches[0]


def _write_document(path, content, mode=None):
    created = False
    try:
        if mode is None:
            with path.open("x", encoding="utf-8") as destination:
                created = True
                destination.write(content)
                destination.flush()
                os.fsync(destination.fileno())
            if os.name != "nt":
                os.chmod(path, 0o600)
            return
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
        created = True
        with os.fdopen(descriptor, "w", encoding="utf-8") as destination:
            destination.write(content)
            destination.flush()
            os.fsync(destination.fileno())
    except BaseException:
        if created:
            try:
                path.unlink()
            except OSError:
                pass
        raise


def _rollback_directory(destination, filenames):
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


def create_machine(
    name,
    display_name=None,
    portable_facts=None,
    context_id=None,
    *,
    machines_root=None
):
    machine = build_machine_context(name, display_name, portable_facts, context_id)
    files = render_machine_files(machine)
    root = _machines_root(machines_root, create=True)
    destination = root / machine.name
    if os.path.lexists(destination):
        raise MachineContextError(f"Machine context '{machine.name}' already exists.")
    created = False
    created_files = []
    try:
        destination.mkdir(mode=0o700)
        created = True
        _write_document(destination / "metadata.toml", files["metadata.toml"])
        created_files.append("metadata.toml")
        local = destination / "local"
        local.mkdir(mode=0o700)
        (destination / "shareable").mkdir(mode=0o700)
        for filename in ("identity.md", "software.toml", "learned.md"):
            _write_document(local / filename, files[filename])
    except BaseException as error:
        rollback_errors = ()
        if created:
            try:
                shutil.rmtree(destination)
            except OSError as rollback_error:
                rollback_errors = (str(rollback_error),)
        if isinstance(error, (KeyboardInterrupt, SystemExit)):
            raise
        message = f"Could not create machine context '{machine.name}': {error}"
        if rollback_errors:
            message += "\nRollback could not remove all new artifacts:\n"
            message += "\n".join(rollback_errors)
        raise MachineContextError(message) from None
    return destination


def local_machines_directory(*, target_config=None):
    try:
        path = config_path() if target_config is None else Path(target_config).expanduser()
    except ConfigError as error:
        raise MachineContextError(str(error)) from None
    if not path.is_absolute():
        raise MachineContextError("RotBot configuration path must be absolute.")
    return path.parent / "machines"


def local_machine_record_path(name, *, target_config=None):
    name = _validate_identifier(name, "name")
    return local_machines_directory(target_config=target_config) / f"{name}.toml"


def _reject_secret_fields(value):
    prohibited = (
        "password", "passwd", "private_key", "api_key", "token", "secret",
        "credential", "cookie", "recovery_code", "password_hash"
    )
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = key.lower().replace("-", "_")
            if any(term in normalized for term in prohibited):
                raise MachineContextError(f"Authentication secrets are not allowed: {key}")
            _reject_secret_fields(child)
    elif isinstance(value, list):
        for child in value:
            _reject_secret_fields(child)


def validate_local_facts(local_facts=None):
    if local_facts is None:
        return {}
    if not isinstance(local_facts, dict):
        raise MachineContextError("Invalid local machine facts.")
    unknown = set(local_facts) - set(LOCAL_SECTIONS)
    if unknown:
        raise MachineContextError(f"Unsupported local machine section: {sorted(unknown)[0]}")
    _reject_secret_fields(local_facts)
    normalized = {}
    connection = local_facts.get("connection")
    if connection:
        if not isinstance(connection, dict):
            raise MachineContextError("Invalid local machine connection facts.")
        allowed_connection = {
            "hostname",
            "tailscale_name",
            "ssh_host",
            "ssh_user",
            "ssh_available",
            "auth_method"
        }
        if set(connection) - allowed_connection:
            raise MachineContextError("Unsupported local machine connection field.")
        parsed = {}
        for key, value in connection.items():
            if key == "ssh_available":
                if not isinstance(value, bool):
                    raise MachineContextError("Invalid SSH availability value.")
                parsed[key] = value
            else:
                parsed[key] = _validate_text(value, f"connection {key}")
        normalized["connection"] = parsed
    schemas = {
        "network": {"interface", "address"},
        "services": {"name", "port", "protocol", "access", "description"},
        "users": {"username", "role"}
    }
    for section, allowed in schemas.items():
        entries = local_facts.get(section)
        if not entries:
            continue
        if not isinstance(entries, list):
            raise MachineContextError(f"Invalid local machine {section} facts.")
        parsed_entries = []
        for entry in entries:
            if not isinstance(entry, dict) or not entry or set(entry) - allowed:
                raise MachineContextError(f"Invalid local machine {section} entry.")
            parsed = {}
            for key, value in entry.items():
                if key == "port":
                    if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 65535:
                        raise MachineContextError("Invalid local machine service port.")
                    parsed[key] = value
                else:
                    parsed[key] = _validate_text(value, f"{section} {key}")
            parsed_entries.append(parsed)
        normalized[section] = parsed_entries
    return normalized


def has_local_facts(local_facts):
    return bool(validate_local_facts(local_facts))


def render_local_machine_record(name, local_facts, context_id=None):
    name = _validate_identifier(name, "name")
    if context_id is not None:
        try:
            context_id = validate_context_id(context_id)
        except ContextIdentifierError as error:
            raise MachineContextError(str(error)) from None
    facts = validate_local_facts(local_facts)
    if not facts:
        raise MachineContextError("No local machine facts are available to store.")
    lines = [f"machine_ref = {_toml_value(context_id or name)}"]
    connection = facts.get("connection")
    if connection:
        lines.extend(("", "[connection]"))
        for key, value in connection.items():
            lines.append(f"{key} = {_toml_value(value)}")
    for section in ("network", "services", "users"):
        for entry in facts.get(section, ()):
            lines.extend(("", f"[[{section}]]"))
            for key, value in entry.items():
                lines.append(f"{key} = {_toml_value(value)}")
    content = "\n".join(lines) + "\n"
    try:
        tomllib.loads(content)
    except tomllib.TOMLDecodeError as error:
        raise MachineContextError(f"Could not render valid local machine TOML: {error}") from None
    return content


def create_local_machine_record(name, local_facts, context_id=None, *, target_config=None):
    content = render_local_machine_record(name, local_facts, context_id)
    root = local_machines_directory(target_config=target_config)
    if root.is_symlink():
        raise MachineContextError(f"Invalid local machine directory: {root}")
    try:
        root.mkdir(parents=True, mode=0o700, exist_ok=True)
    except OSError as error:
        raise MachineContextError(f"Could not create local machine directory: {error}") from None
    if root.is_symlink() or not root.is_dir():
        raise MachineContextError(f"Invalid local machine directory: {root}")
    destination = root / f"{_validate_identifier(name, 'name')}.toml"
    if os.path.lexists(destination):
        raise MachineContextError(f"Local machine record '{name}' already exists.")
    try:
        _write_document(destination, content, mode=0o600)
    except BaseException as error:
        if isinstance(error, (KeyboardInterrupt, SystemExit)):
            raise
        raise MachineContextError(f"Could not create local machine record '{name}': {error}") from None
    return destination


def _load_local_machine_record_entry(name, *, target_config=None):
    path = local_machine_record_path(name, target_config=target_config)
    if target_config is None and not os.path.lexists(path):
        legacy = legacy_config_path().parent / "machines" / f"{name}.toml"
        if os.path.lexists(legacy):
            path = legacy
    if not os.path.lexists(path):
        return None
    if path.is_symlink() or not path.is_file():
        raise MachineContextError(f"Invalid local machine record: {name}")
    if os.name != "nt" and stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise MachineContextError(f"Local machine record is not private: {name}")
    try:
        document = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as error:
        raise MachineContextError(f"Could not load local machine record '{name}': {error}") from None
    reference = document.get("machine_ref")
    if not isinstance(reference, str):
        raise MachineContextError(f"Invalid local machine reference: {name}")
    facts = {section: document[section] for section in LOCAL_SECTIONS if section in document}
    return reference, validate_local_facts(facts)


def load_local_machine_record(name, *, target_config=None):
    entry = _load_local_machine_record_entry(name, target_config=target_config)
    return entry[1] if entry is not None else None


def associated_machine_context(local_facts):
    hostname = local_facts.get("connection", {}).get("hostname")
    if not hostname:
        return None
    matches = []
    matched_ids = set()
    for target in (config_path(), legacy_config_path()):
        root = target.parent / "machines"
        if root.is_symlink() or not root.is_dir():
            continue
        try:
            entries = tuple(root.glob("*.toml"))
        except OSError as error:
            raise MachineContextError(
                f"Could not list local machine records: {error}"
            ) from None
        for entry in entries:
            name = entry.stem
            try:
                entry = _load_local_machine_record_entry(name, target_config=target)
                if entry is None:
                    continue
                reference, facts = entry
                machine = load_machine_context_reference(reference)
            except MachineContextError:
                continue
            if (
                facts
                and facts.get("connection", {}).get("hostname") == hostname
                and machine.id not in matched_ids
            ):
                matches.append(machine)
                matched_ids.add(machine.id)
    if len(matches) > 1:
        names = ", ".join(sorted(machine.name for machine in matches))
        raise MachineContextError(
            f"Multiple local machine records match hostname '{hostname}': {names}"
        )
    return matches[0] if matches else None
