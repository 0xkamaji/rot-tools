from dataclasses import dataclass
import json
import os
from pathlib import Path
import stat
import tempfile
import tomllib

from rotbot.contexts.inspection import IdentificationSources, InspectedContext
from rotbot.contexts.paths import PathConfigurationError, state_root


SCHEMA_VERSION = 1
CONTEXT_TYPES = ("assistant", "user", "machine", "project")


class SessionStateError(Exception):
    pass


def session_state_path(environ=None):
    try:
        return state_root(environ) / "session.toml"
    except PathConfigurationError as error:
        raise SessionStateError(str(error)) from None


@dataclass(frozen=True)
class SessionState:
    cwd: Path
    assistant: str | None
    assistant_id: str | None
    assistant_source: str
    user: str | None
    user_id: str | None
    user_source: str
    machine: str | None
    machine_id: str | None
    machine_source: str
    project: str | None
    project_id: str | None
    project_source: str

    @classmethod
    def from_inspected(cls, inspected):
        sources = inspected.identification_sources
        return cls(
            Path(inspected.cwd),
            inspected.assistant, inspected.assistant_id, sources.assistant,
            inspected.user, inspected.user_id, sources.user,
            inspected.machine, inspected.machine_id, sources.machine,
            inspected.project, inspected.project_id, sources.project
        )

    def to_inspected(self):
        return InspectedContext(
            self.assistant, self.assistant_id,
            self.user, self.user_id,
            self.machine, self.machine_id,
            self.project, self.project_id,
            self.cwd,
            IdentificationSources(
                self.assistant_source,
                self.user_source,
                self.machine_source,
                self.project_source
            ),
            ()
        )


def _optional_string(value, label):
    if value is None:
        return None
    if (
        not isinstance(value, str)
        or not value
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
        or any(0xD800 <= ord(character) <= 0xDFFF for character in value)
    ):
        raise SessionStateError(f"Invalid session state {label}.")
    return value


def _source(value, label):
    value = _optional_string(value, label)
    if value is None:
        raise SessionStateError(f"Missing session state {label}.")
    return value


def _validated(state):
    if not isinstance(state, SessionState):
        raise SessionStateError("Invalid session state value.")
    cwd = Path(state.cwd)
    cwd_text = str(cwd)
    if (
        not cwd.is_absolute()
        or any(ord(character) < 32 or ord(character) == 127 for character in cwd_text)
        or any(0xD800 <= ord(character) <= 0xDFFF for character in cwd_text)
    ):
        raise SessionStateError("Session state cwd must be an absolute path.")
    values = {"cwd": cwd}
    for context_type in CONTEXT_TYPES:
        values[context_type] = _optional_string(
            getattr(state, context_type), f"{context_type} name"
        )
        values[f"{context_type}_id"] = _optional_string(
            getattr(state, f"{context_type}_id"), f"{context_type} ID"
        )
        values[f"{context_type}_source"] = _source(
            getattr(state, f"{context_type}_source"), f"{context_type} source"
        )
    return values


def _render(state):
    values = _validated(state)
    lines = [
        f"schema_version = {SCHEMA_VERSION}",
        f"cwd = {json.dumps(str(values['cwd']), ensure_ascii=False)}"
    ]
    for context_type in CONTEXT_TYPES:
        lines.extend(("", f"[{context_type}]"))
        if values[f"{context_type}_id"] is not None:
            lines.append(
                f"id = {json.dumps(values[f'{context_type}_id'], ensure_ascii=False)}"
            )
        if values[context_type] is not None:
            lines.append(
                f"name = {json.dumps(values[context_type], ensure_ascii=False)}"
            )
        lines.append(
            f"source = {json.dumps(values[f'{context_type}_source'], ensure_ascii=False)}"
        )
    return "\n".join(lines) + "\n"


def _parse(content):
    try:
        document = tomllib.loads(content)
    except tomllib.TOMLDecodeError as error:
        raise SessionStateError(f"Invalid session state TOML: {error}") from None
    if type(document.get("schema_version")) is not int or document["schema_version"] != 1:
        raise SessionStateError("Unsupported session state schema version.")
    allowed = {"schema_version", "cwd", *CONTEXT_TYPES}
    if set(document) - allowed:
        raise SessionStateError("Session state contains unsupported fields.")
    cwd_value = document.get("cwd")
    if not isinstance(cwd_value, str):
        raise SessionStateError("Invalid session state cwd.")
    cwd = Path(cwd_value)
    if (
        not cwd.is_absolute()
        or any(ord(character) < 32 or ord(character) == 127 for character in cwd_value)
        or any(0xD800 <= ord(character) <= 0xDFFF for character in cwd_value)
    ):
        raise SessionStateError("Session state cwd must be an absolute path.")
    values = {"cwd": cwd}
    for context_type in CONTEXT_TYPES:
        table = document.get(context_type)
        if not isinstance(table, dict) or set(table) - {"id", "name", "source"}:
            raise SessionStateError(f"Invalid session state {context_type} table.")
        values[context_type] = _optional_string(
            table.get("name"), f"{context_type} name"
        )
        values[f"{context_type}_id"] = _optional_string(
            table.get("id"), f"{context_type} ID"
        )
        values[f"{context_type}_source"] = _source(
            table.get("source"), f"{context_type} source"
        )
    return SessionState(
        values["cwd"],
        values["assistant"], values["assistant_id"], values["assistant_source"],
        values["user"], values["user_id"], values["user_source"],
        values["machine"], values["machine_id"], values["machine_source"],
        values["project"], values["project_id"], values["project_source"]
    )


class SessionStateStore:
    def __init__(self, path=None):
        self.path = session_state_path() if path is None else Path(path)

    def load(self):
        if not os.path.lexists(self.path):
            return None
        try:
            if self.path.is_symlink() or not self.path.is_file():
                raise SessionStateError(f"Invalid session state file: {self.path}")
            if os.name != "nt" and stat.S_IMODE(self.path.stat().st_mode) & 0o077:
                raise SessionStateError(f"Session state file is not private: {self.path}")
            content = self.path.read_text(encoding="utf-8")
        except SessionStateError:
            raise
        except (OSError, UnicodeError) as error:
            raise SessionStateError(f"Could not read session state: {error}") from None
        return _parse(content)

    def save(self, state):
        temporary_path = None
        try:
            content = _render(state)
            if self.path.is_symlink():
                raise SessionStateError(f"Invalid session state file: {self.path}")
            self.path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
            if self.path.parent.is_symlink() or not self.path.parent.is_dir():
                raise SessionStateError(
                    f"Invalid session state directory: {self.path.parent}"
                )
            if os.name != "nt":
                os.chmod(self.path.parent, 0o700)
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self.path.parent,
                prefix="session.", suffix=".tmp", delete=False
            ) as temporary:
                temporary_path = Path(temporary.name)
                os.chmod(temporary_path, 0o600)
                temporary.write(content)
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary_path, self.path)
            temporary_path = None
            if os.name != "nt":
                os.chmod(self.path, 0o600)
        except SessionStateError:
            raise
        except (OSError, UnicodeError) as error:
            raise SessionStateError(f"Could not write session state: {error}") from None
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink()
                except OSError:
                    pass
