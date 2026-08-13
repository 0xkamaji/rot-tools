import json
import os
from pathlib import Path
import re
import stat
import tempfile
import tomllib

from rotbot.contexts.identifiers import ContextIdentifierError, validate_context_id
from rotbot.contexts.paths import config_root


CONFIG_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
CONFIG_KEYS = ("source_path", "production_path")
LOCAL_CONTEXT_TYPES = ("user", "assistant", "machine")
AGENT_TRUST_VALUES = ("external", "trusted_private")


class ConfigError(Exception):
    pass


def _config_base(environ=None):
    environ = os.environ if environ is None else environ
    xdg_home = environ.get("XDG_CONFIG_HOME")
    if xdg_home:
        base = Path(xdg_home).expanduser()
        if not base.is_absolute():
            raise ConfigError("XDG_CONFIG_HOME must be an absolute path.")
    else:
        base = Path.home() / ".config"
    return base


def config_path(environ=None):
    try:
        return config_root(environ) / "config.toml"
    except Exception as error:
        if error.__class__.__name__ == "PathConfigurationError":
            raise ConfigError(str(error)) from None
        raise


def legacy_config_path(environ=None):
    return _config_base(environ) / "rot" / "config.toml"


def _config_read_path(path):
    path = Path(path)
    if not path.exists() and path == config_path():
        legacy = legacy_config_path()
        if legacy.exists() or legacy.is_symlink():
            return legacy
    return path


def load_config(path=None):
    path = config_path() if path is None else Path(path)
    source = _config_read_path(path)
    if source.is_symlink():
        raise ConfigError(f"RotBot configuration must not be a symlink:\n{source}")
    if not source.exists():
        return {}
    try:
        content = source.read_bytes()
        return tomllib.loads(content.decode("utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as error:
        raise ConfigError(f"Could not read RotBot configuration:\n{source}\n{error}") from None


def _validate_context_binding(name, binding):
    if not isinstance(binding, dict):
        raise ConfigError(f"RotBot context configuration must be a table: {name}")
    for key, value in binding.items():
        if key in CONFIG_KEYS:
            if not isinstance(value, str):
                raise ConfigError(f"RotBot context path must be a string: {name}.{key}")
            if (
                not value
                or "\0" in value
                or not Path(value).expanduser().is_absolute()
            ):
                raise ConfigError(f"RotBot context path must be absolute: {name}.{key}")
    return binding


def get_context_bindings(path=None):
    contexts = load_config(path).get("contexts", {})
    if not isinstance(contexts, dict):
        raise ConfigError("RotBot configuration 'contexts' value must be a table.")
    for name, binding in contexts.items():
        if not isinstance(name, str) or not CONFIG_NAME_PATTERN.fullmatch(name):
            raise ConfigError(f"Invalid context name in RotBot configuration: {name}")
        _validate_context_binding(name, binding)
    return contexts


def get_context_binding(name, path=None):
    document = load_config(path)
    contexts = document.get("contexts", {})
    if not isinstance(contexts, dict):
        raise ConfigError("RotBot configuration 'contexts' value must be a table.")
    binding = contexts.get(name)
    return {} if binding is None else _validate_context_binding(name, binding)


def get_local_context_bindings(path=None):
    document = load_config(path)
    bindings = {}
    for context_type in LOCAL_CONTEXT_TYPES:
        section = document.get(context_type)
        if section is not None:
            if not isinstance(section, dict):
                raise ConfigError(
                    f"RotBot configuration '{context_type}' value must be a table."
                )
            context_id = section.get("id")
            if not isinstance(context_id, str) or not CONFIG_NAME_PATTERN.fullmatch(
                context_id
            ):
                raise ConfigError(f"Invalid local {context_type} context ID: {context_id}")
            bindings[context_type] = context_id

    defaults = document.get("defaults", {})
    if not isinstance(defaults, dict):
        raise ConfigError("RotBot configuration 'defaults' value must be a table.")
    for context_type in LOCAL_CONTEXT_TYPES:
        if context_type in bindings or context_type not in defaults:
            continue
        value = defaults[context_type]
        if not isinstance(value, str) or not CONFIG_NAME_PATTERN.fullmatch(value):
            raise ConfigError(f"Invalid RotBot default {context_type}: {value}")
        bindings[context_type] = value
    return bindings


def get_defaults(path=None):
    return get_local_context_bindings(path)


def get_agent_trust(agent_name, path=None):
    if not isinstance(agent_name, str) or not CONFIG_NAME_PATTERN.fullmatch(agent_name):
        raise ConfigError(f"Invalid AI agent name: {agent_name}")
    document = load_config(path)
    ai = document.get("ai", {})
    if not isinstance(ai, dict):
        raise ConfigError("RotBot configuration 'ai' value must be a table.")
    agents = ai.get("agents", {})
    if not isinstance(agents, dict):
        raise ConfigError("RotBot configuration 'ai.agents' value must be a table.")
    configured = agents.get(agent_name)
    if configured is None:
        return "external"
    if not isinstance(configured, dict):
        raise ConfigError(
            f"RotBot configuration 'ai.agents.{agent_name}' must be a table."
        )
    trust = configured.get("trust", "external")
    if trust not in AGENT_TRUST_VALUES:
        raise ConfigError(
            f"Invalid trust for AI agent '{agent_name}': {trust}. "
            "Expected external or trusted_private."
        )
    return trust


def _table_header(name):
    if not CONFIG_NAME_PATTERN.fullmatch(name):
        raise ConfigError(f"Invalid context name: {name}")
    if "." not in name:
        return f"[contexts.{name}]"
    return f"[contexts.{json.dumps(name, ensure_ascii=False)}]"


def _table_range(lines, name):
    def is_target_header(line):
        content = line.split("#", 1)[0].strip()
        if not content.startswith("[") or not content.endswith("]"):
            return False
        body = content[1:-1]
        try:
            parsed = tomllib.loads(f"[{body}]\n_marker = true\n")
        except tomllib.TOMLDecodeError:
            return False
        return parsed.get("contexts", {}).get(name, {}).get("_marker") is True

    start = next(
        (
            index
            for index, line in enumerate(lines)
            if is_target_header(line)
        ),
        None
    )
    if start is None:
        return None
    end = next(
        (
            index
            for index in range(start + 1, len(lines))
            if lines[index].lstrip().startswith("[")
        ),
        len(lines)
    )
    return start, end


def _updated_config_text(original, name, key, value):
    if key not in CONFIG_KEYS:
        raise ConfigError(f"Unsupported context binding key: {key}")
    lines = original.splitlines(keepends=True)
    table_range = _table_range(lines, name)
    assignment = f"{key} = {json.dumps(value, ensure_ascii=False)}\n"

    if table_range is None:
        parsed = tomllib.loads(original) if original.strip() else {}
        if name in parsed.get("contexts", {}):
            raise ConfigError(
                f"Could not safely update existing context configuration: {name}"
            )
        prefix = original
        if prefix and not prefix.endswith("\n"):
            prefix += "\n"
        if prefix and not prefix.endswith("\n\n"):
            prefix += "\n"
        return f"{prefix}{_table_header(name)}\n{assignment}"

    start, end = table_range
    key_pattern = re.compile(rf"^\s*{re.escape(key)}\s*=")
    existing = [
        index
        for index in range(start + 1, end)
        if key_pattern.match(lines[index])
    ]
    if len(existing) > 1:
        raise ConfigError(f"Duplicate context binding key: {name}.{key}")
    if existing:
        lines[existing[0]] = assignment
    else:
        if end > 0 and lines[end - 1] and not lines[end - 1].endswith("\n"):
            lines[end - 1] += "\n"
        lines.insert(end, assignment)
    return "".join(lines)


def _write_config(path, updated, mode):
    temporary_path = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix="config.",
            suffix=".tmp",
            delete=False
        ) as temporary:
            temporary_path = Path(temporary.name)
            os.chmod(temporary_path, mode)
            temporary.write(updated)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, path)
        temporary_path = None
    except OSError as error:
        raise ConfigError(f"Could not write RotBot configuration:\n{path}\n{error}") from None
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except OSError:
                pass


def _config_text_for_update(path):
    source = _config_read_path(path)
    if not source.exists():
        return "", 0o600
    load_config(source)
    try:
        return (
            source.read_text(encoding="utf-8"),
            0o600
        )
    except (OSError, UnicodeError) as error:
        raise ConfigError(f"Could not read RotBot configuration:\n{source}\n{error}") from None


def _local_table_range(lines, context_type):
    def is_target_header(line):
        content = line.split("#", 1)[0].strip()
        if not content.startswith("[") or not content.endswith("]"):
            return False
        body = content[1:-1]
        try:
            parsed = tomllib.loads(f"[{body}]\n_marker = true\n")
        except tomllib.TOMLDecodeError:
            return False
        return parsed.get(context_type, {}).get("_marker") is True

    start = next(
        (
            index
            for index, line in enumerate(lines)
            if is_target_header(line)
        ),
        None
    )
    if start is None:
        return None
    end = next(
        (
            index
            for index in range(start + 1, len(lines))
            if lines[index].lstrip().startswith("[")
        ),
        len(lines)
    )
    return start, end


def _updated_local_context_text(original, context_type, context_id):
    lines = original.splitlines(keepends=True)
    table_range = _local_table_range(lines, context_type)
    assignment = f"id = {json.dumps(context_id, ensure_ascii=False)}\n"
    if table_range is None:
        parsed = tomllib.loads(original) if original.strip() else {}
        if context_type in parsed:
            raise ConfigError(
                f"Could not safely update local {context_type} configuration."
            )
        prefix = original
        if prefix and not prefix.endswith("\n"):
            prefix += "\n"
        if prefix and not prefix.endswith("\n\n"):
            prefix += "\n"
        return f"{prefix}[{context_type}]\n{assignment}"

    start, end = table_range
    existing = [
        index
        for index in range(start + 1, end)
        if re.match(r"^\s*id\s*=", lines[index])
    ]
    if len(existing) > 1:
        raise ConfigError(f"Duplicate local {context_type} context ID.")
    if existing:
        lines[existing[0]] = assignment
    else:
        if end > 0 and lines[end - 1] and not lines[end - 1].endswith("\n"):
            lines[end - 1] += "\n"
        lines.insert(end, assignment)
    return "".join(lines)


def set_local_context_binding(context_type, context_id, path=None):
    if context_type not in LOCAL_CONTEXT_TYPES:
        raise ConfigError(f"Unsupported local context type: {context_type}")
    try:
        context_id = validate_context_id(context_id)
    except ContextIdentifierError as error:
        raise ConfigError(str(error)) from None
    path = config_path() if path is None else Path(path)
    if path.is_symlink():
        raise ConfigError(f"RotBot configuration must not be a symlink:\n{path}")
    original, mode = _config_text_for_update(path)
    updated = _updated_local_context_text(original, context_type, context_id)
    try:
        proposed = tomllib.loads(updated)
    except tomllib.TOMLDecodeError as error:
        raise ConfigError(f"Could not safely update RotBot configuration:\n{error}") from None
    if proposed.get(context_type, {}).get("id") != context_id:
        raise ConfigError("RotBot local context update did not validate.")
    _write_config(path, updated, mode)


def set_context_binding(name, key, value, path=None):
    path = config_path() if path is None else Path(path)
    if path.is_symlink():
        raise ConfigError(f"RotBot configuration must not be a symlink:\n{path}")

    load_config(path)
    get_context_binding(name, path)
    original, mode = _config_text_for_update(path)

    updated = _updated_config_text(original, name, key, value)
    try:
        proposed = tomllib.loads(updated)
    except tomllib.TOMLDecodeError as error:
        raise ConfigError(f"Could not safely update RotBot configuration:\n{error}") from None

    contexts = proposed.get("contexts", {})
    if contexts.get(name, {}).get(key) != value:
        raise ConfigError("RotBot configuration update did not validate.")
    _write_config(path, updated, mode)


def remove_context_bindings(name, path=None):
    path = config_path() if path is None else Path(path)
    if path.is_symlink():
        raise ConfigError(f"RotBot configuration must not be a symlink:\n{path}")
    document = load_config(path)
    get_context_binding(name, path)
    if name not in document.get("contexts", {}):
        return False

    original, mode = _config_text_for_update(path)
    lines = original.splitlines(keepends=True)
    table_range = _table_range(lines, name)
    if table_range is None:
        raise ConfigError(f"Could not safely remove context configuration: {name}")
    start, end = table_range
    del lines[start:end]
    updated = "".join(lines)
    try:
        proposed = tomllib.loads(updated) if updated.strip() else {}
    except tomllib.TOMLDecodeError as error:
        raise ConfigError(f"Could not safely update RotBot configuration:\n{error}") from None
    if name in proposed.get("contexts", {}):
        raise ConfigError("RotBot configuration removal did not validate.")
    _write_config(path, updated, mode)
    return True
