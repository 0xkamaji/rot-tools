from dataclasses import dataclass, field
from datetime import datetime
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import tomllib


CONVERSATION_ID_PATTERN = re.compile(r"^rotconv_[0-9a-f]{32}$")
MESSAGE_ID_PATTERN = re.compile(r"^msg_[0-9a-f]{32}$")


class ConversationStoreError(Exception):
    pass


def conversations_path(environ=None):
    environ = os.environ if environ is None else environ
    xdg_home = environ.get("XDG_DATA_HOME")
    if xdg_home:
        base = Path(xdg_home).expanduser()
        if not base.is_absolute():
            raise ConversationStoreError("XDG_DATA_HOME must be an absolute path.")
    else:
        base = Path.home() / ".local" / "share"
    return base / "rotbot" / "conversations"


def _toml_string(value):
    return json.dumps(str(value), ensure_ascii=False)


def _optional(value):
    return "" if value is None else value


@dataclass(frozen=True)
class StoredMessage:
    id: str
    role: str
    created_at: datetime
    content: str
    authority: str
    status: str


@dataclass
class StoredConversation:
    id: str
    status: str
    started_at: datetime
    closed_at: datetime | None
    user_id: str | None
    assistant_id: str | None
    machine_id: str | None
    project_id: str | None
    initial_cwd: str
    current_cwd: str
    backend: str
    model: str | None
    context_fingerprint: str | None
    context_version: int
    remote_state: list[dict] = field(default_factory=list)
    messages: list[StoredMessage] = field(default_factory=list)


class ConversationStore:
    def __init__(self, root=None):
        self.root = conversations_path() if root is None else Path(root)

    def _directory(self, conversation_id):
        if not CONVERSATION_ID_PATTERN.fullmatch(conversation_id):
            raise ConversationStoreError(
                f"Invalid Rot conversation ID: {conversation_id}"
            )
        return self.root / conversation_id

    def _ensure_root(self):
        try:
            self.root.mkdir(parents=True, mode=0o700, exist_ok=True)
            if self.root.is_symlink() or not self.root.is_dir():
                raise ConversationStoreError(
                    f"Invalid conversation storage directory: {self.root}"
                )
            if os.name != "nt":
                os.chmod(self.root, 0o700)
        except ConversationStoreError:
            raise
        except OSError as error:
            raise ConversationStoreError(
                f"Could not prepare conversation storage: {error}"
            ) from None

    def create(self, conversation_id, started_at, inspected, cwd, backend):
        self._ensure_root()
        directory = self._directory(conversation_id)
        try:
            directory.mkdir(mode=0o700)
            if os.name != "nt":
                os.chmod(directory, 0o700)
            transcript = directory / "transcript.jsonl"
            descriptor = os.open(transcript, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(descriptor)
            metadata = {
                "id": conversation_id,
                "status": "active",
                "started_at": started_at.isoformat(),
                "closed_at": "",
                "user_id": _optional(inspected.user_id),
                "assistant_id": _optional(inspected.assistant_id),
                "machine_id": _optional(inspected.machine_id),
                "project_id": _optional(inspected.project_id),
                "initial_cwd": str(cwd),
                "current_cwd": str(cwd),
                "backend": backend.lower(),
                "model": "",
                "context_fingerprint": "",
                "context_version": 0,
                "remote_state": []
            }
            self._write_metadata(directory, metadata)
        except FileExistsError:
            raise ConversationStoreError(
                f"Rot conversation already exists: {conversation_id}"
            ) from None
        except ConversationStoreError:
            self._remove_incomplete(directory)
            raise
        except OSError as error:
            self._remove_incomplete(directory)
            raise ConversationStoreError(
                f"Could not create Rot conversation: {error}"
            ) from None

    def _remove_incomplete(self, directory):
        try:
            for name in ("metadata.toml", "transcript.jsonl"):
                path = directory / name
                if path.is_file() and not path.is_symlink():
                    path.unlink()
            directory.rmdir()
        except OSError:
            pass

    def _metadata_text(self, metadata):
        keys = (
            "id", "status", "started_at", "closed_at", "user_id",
            "assistant_id", "machine_id", "project_id", "initial_cwd",
            "current_cwd", "backend", "model", "context_fingerprint"
        )
        lines = [f"schema_version = 1"]
        lines.extend(f"{key} = {_toml_string(metadata.get(key, ''))}" for key in keys)
        lines.append(f"context_version = {int(metadata.get('context_version', 0))}")
        for reference in metadata.get("remote_state", []):
            lines.append("")
            lines.append("[[remote_state]]")
            for key in ("layer", "provider", "type", "id", "persistence"):
                lines.append(f"{key} = {_toml_string(reference.get(key, ''))}")
        return "\n".join(lines) + "\n"

    def _write_metadata(self, directory, metadata):
        path = directory / "metadata.toml"
        temporary_path = None
        try:
            if path.is_symlink():
                raise ConversationStoreError(f"Invalid conversation metadata: {path}")
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=directory,
                prefix="metadata.", suffix=".tmp", delete=False
            ) as temporary:
                temporary_path = Path(temporary.name)
                os.chmod(temporary_path, 0o600)
                temporary.write(self._metadata_text(metadata))
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary_path, path)
            temporary_path = None
            if os.name != "nt":
                os.chmod(path, 0o600)
        except ConversationStoreError:
            raise
        except OSError as error:
            raise ConversationStoreError(
                f"Could not update conversation metadata: {error}"
            ) from None
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink()
                except OSError:
                    pass

    def _read_metadata(self, conversation_id):
        directory = self._directory(conversation_id)
        path = directory / "metadata.toml"
        try:
            if directory.is_symlink() or not directory.is_dir():
                raise ConversationStoreError(
                    f"Rot conversation not found: {conversation_id}"
                )
            if path.is_symlink() or not path.is_file():
                raise ConversationStoreError(f"Invalid conversation metadata: {path}")
            if os.name != "nt" and stat.S_IMODE(path.stat().st_mode) & 0o077:
                raise ConversationStoreError(
                    f"Conversation metadata is not private: {path}"
                )
            metadata = tomllib.loads(path.read_text(encoding="utf-8"))
        except ConversationStoreError:
            raise
        except (OSError, UnicodeError, tomllib.TOMLDecodeError) as error:
            raise ConversationStoreError(
                f"Could not read conversation metadata: {error}"
            ) from None
        if metadata.get("id") != conversation_id or metadata.get("schema_version") != 1:
            raise ConversationStoreError(f"Invalid conversation metadata: {path}")
        required = {
            "status": str, "started_at": str, "closed_at": str,
            "initial_cwd": str, "current_cwd": str, "backend": str,
            "context_version": int
        }
        if (
            any(not isinstance(metadata.get(key), kind) for key, kind in required.items())
            or not isinstance(metadata.get("remote_state", []), list)
        ):
            raise ConversationStoreError(f"Invalid conversation metadata: {path}")
        return directory, metadata

    def update_metadata(self, conversation_id, **changes):
        directory, metadata = self._read_metadata(conversation_id)
        metadata.update(changes)
        self._write_metadata(directory, metadata)

    def append_message(self, conversation_id, message):
        record = {
            "type": "message",
            "id": message.id,
            "role": message.role,
            "created_at": message.created_at.isoformat(),
            "content": message.content,
            "authority": message.authority,
            "status": message.status
        }
        self._append_record(conversation_id, record)

    def update_message_status(self, conversation_id, message_id, status):
        if not MESSAGE_ID_PATTERN.fullmatch(message_id):
            raise ConversationStoreError(f"Invalid Rot message ID: {message_id}")
        self._append_record(conversation_id, {
            "type": "message_status",
            "message_id": message_id,
            "created_at": datetime.now().astimezone().isoformat(),
            "status": status
        })

    def _append_record(self, conversation_id, record):
        directory, _metadata = self._read_metadata(conversation_id)
        path = directory / "transcript.jsonl"
        payload = (json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8")
        descriptor = None
        try:
            if path.is_symlink() or not path.is_file():
                raise ConversationStoreError(f"Invalid conversation transcript: {path}")
            if os.name != "nt" and stat.S_IMODE(path.stat().st_mode) & 0o077:
                raise ConversationStoreError(
                    f"Conversation transcript is not private: {path}"
                )
            descriptor = os.open(path, os.O_WRONLY | os.O_APPEND)
            written = 0
            while written < len(payload):
                written += os.write(descriptor, payload[written:])
            os.fsync(descriptor)
        except ConversationStoreError:
            raise
        except OSError as error:
            raise ConversationStoreError(
                f"Could not append conversation transcript: {error}"
            ) from None
        finally:
            if descriptor is not None:
                os.close(descriptor)

    def close(self, conversation_id, closed_at, **changes):
        self.update_metadata(
            conversation_id,
            status="closed",
            closed_at=closed_at.isoformat(),
            **changes
        )

    def load(self, conversation_id):
        directory, metadata = self._read_metadata(conversation_id)
        path = directory / "transcript.jsonl"
        try:
            if path.is_symlink() or not path.is_file():
                raise ConversationStoreError(f"Invalid conversation transcript: {path}")
            if os.name != "nt" and stat.S_IMODE(path.stat().st_mode) & 0o077:
                raise ConversationStoreError(
                    f"Conversation transcript is not private: {path}"
                )
            records = [
                json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
        except ConversationStoreError:
            raise
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise ConversationStoreError(
                f"Could not read conversation transcript: {error}"
            ) from None
        messages = []
        positions = {}
        for record in records:
            if not isinstance(record, dict):
                raise ConversationStoreError(
                    f"Invalid conversation transcript record: {record!r}"
                )
            if record.get("type") == "message":
                try:
                    message = StoredMessage(
                        id=record["id"], role=record["role"],
                        created_at=datetime.fromisoformat(record["created_at"]),
                        content=record["content"], authority=record["authority"],
                        status=record["status"]
                    )
                except (KeyError, TypeError, ValueError) as error:
                    raise ConversationStoreError(
                        f"Invalid conversation transcript record: {error}"
                    ) from None
                positions[message.id] = len(messages)
                messages.append(message)
            elif record.get("type") == "message_status":
                index = positions.get(record.get("message_id"))
                if index is not None:
                    old = messages[index]
                    messages[index] = StoredMessage(
                        old.id, old.role, old.created_at, old.content,
                        old.authority, record.get("status", old.status)
                    )
        try:
            remote_state = metadata.get("remote_state", [])
            if any(not isinstance(reference, dict) for reference in remote_state):
                raise TypeError("remote_state entries must be tables")
            return StoredConversation(
                id=metadata["id"], status=metadata["status"],
                started_at=datetime.fromisoformat(metadata["started_at"]),
                closed_at=(
                    datetime.fromisoformat(metadata["closed_at"])
                    if metadata.get("closed_at") else None
                ),
                user_id=metadata.get("user_id") or None,
                assistant_id=metadata.get("assistant_id") or None,
                machine_id=metadata.get("machine_id") or None,
                project_id=metadata.get("project_id") or None,
                initial_cwd=metadata["initial_cwd"],
                current_cwd=metadata["current_cwd"],
                backend=metadata["backend"], model=metadata.get("model") or None,
                context_fingerprint=metadata.get("context_fingerprint") or None,
                context_version=metadata.get("context_version", 0),
                remote_state=list(remote_state), messages=messages
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ConversationStoreError(
                f"Invalid conversation metadata: {error}"
            ) from None

    def list(self):
        if not os.path.lexists(self.root):
            return []
        if self.root.is_symlink() or not self.root.is_dir():
            raise ConversationStoreError(
                f"Invalid conversation storage directory: {self.root}"
            )
        conversations = []
        try:
            entries = tuple(self.root.iterdir())
        except OSError as error:
            raise ConversationStoreError(
                f"Could not list Rot conversations: {error}"
            ) from None
        for entry in entries:
            if CONVERSATION_ID_PATTERN.fullmatch(entry.name):
                conversations.append(self.load(entry.name))
        return sorted(conversations, key=lambda item: item.started_at, reverse=True)
