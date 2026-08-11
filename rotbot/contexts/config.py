import json
import os
from pathlib import Path
import re
import stat
import tempfile
import tomllib


CONFIG_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
CONFIG_KEYS = ("source_path", "production_path")


class ConfigError(Exception):
    pass


def config_path(environ=None):
    environ = os.environ if environ is None else environ
    xdg_home = environ.get("XDG_CONFIG_HOME")
    if xdg_home:
        base = Path(xdg_home).expanduser()
        if not base.is_absolute():
            raise ConfigError("XDG_CONFIG_HOME must be an absolute path.")
    else:
        base = Path.home() / ".config"
    return base / "rotbot" / "config.toml"


def load_config(path=None):
    path = config_path() if path is None else Path(path)
    if path.is_symlink():
        raise ConfigError(f"RotBot configuration must not be a symlink:\n{path}")
    if not path.exists():
        return {}
    try:
        content = path.read_bytes()
        return tomllib.loads(content.decode("utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as error:
        raise ConfigError(f"Could not read RotBot configuration:\n{path}\n{error}") from None


def get_context_binding(name, path=None):
    document = load_config(path)
    contexts = document.get("contexts", {})
    if not isinstance(contexts, dict):
        raise ConfigError("RotBot configuration 'contexts' value must be a table.")
    binding = contexts.get(name)
    if binding is None:
        return {}
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


def set_context_binding(name, key, value, path=None):
    path = config_path() if path is None else Path(path)
    if path.is_symlink():
        raise ConfigError(f"RotBot configuration must not be a symlink:\n{path}")

    load_config(path)
    get_context_binding(name, path)
    original = ""
    mode = 0o600
    if path.exists():
        try:
            original = path.read_text(encoding="utf-8")
            mode = stat.S_IMODE(path.stat().st_mode)
        except (OSError, UnicodeError) as error:
            raise ConfigError(f"Could not read RotBot configuration:\n{path}\n{error}") from None

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
    if not path.exists() or name not in document.get("contexts", {}):
        return False

    try:
        original = path.read_text(encoding="utf-8")
        mode = stat.S_IMODE(path.stat().st_mode)
    except (OSError, UnicodeError) as error:
        raise ConfigError(f"Could not read RotBot configuration:\n{path}\n{error}") from None
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
