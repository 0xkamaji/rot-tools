import os
from pathlib import Path
import stat
import tempfile

from rotbot.contexts.paths import config_root


DEFAULT_HISTORY_LIMIT = 5000
DEFAULT_DISPLAY_LIMIT = 20


class HistoryError(Exception):
    pass


def history_path():
    return config_root() / "history"


class CommandHistory:
    def __init__(self, path=None, max_entries=DEFAULT_HISTORY_LIMIT):
        self.path = history_path() if path is None else Path(path)
        self.max_entries = max_entries
        self._entries = []
        self.persistence_enabled = True

    def load(self):
        if not os.path.lexists(self.path):
            self._entries = []
            return
        try:
            if self.path.is_symlink() or not self.path.is_file():
                raise HistoryError(f"Invalid command history file: {self.path}")
            if os.name != "nt" and stat.S_IMODE(self.path.stat().st_mode) & 0o077:
                raise HistoryError(f"Command history file is not private: {self.path}")
            entries = self.path.read_text(encoding="utf-8").splitlines()
        except HistoryError:
            raise
        except (OSError, UnicodeError) as error:
            raise HistoryError(f"Could not read command history: {error}") from None
        self._entries = [entry for entry in entries if entry.strip()][-self.max_entries:]

    def add(self, command):
        command = command.strip()
        if not command or "\n" in command or "\r" in command or "\0" in command:
            return False
        if self._entries and self._entries[-1] == command:
            return False
        self._entries.append(command)
        if len(self._entries) > self.max_entries:
            del self._entries[:-self.max_entries]
        return True

    def recent(self, limit=None):
        entries = self._entries if limit is None else self._entries[-limit:]
        return list(entries)

    def save(self):
        if not self.persistence_enabled:
            return
        temporary_path = None
        try:
            if self.path.is_symlink():
                raise HistoryError(f"Invalid command history file: {self.path}")
            self.path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
            if self.path.parent.is_symlink() or not self.path.parent.is_dir():
                raise HistoryError(
                    f"Invalid command history directory: {self.path.parent}"
                )
            content = "".join(f"{entry}\n" for entry in self._entries[-self.max_entries:])
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.path.parent,
                prefix="history.",
                suffix=".tmp",
                delete=False
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
        except HistoryError:
            raise
        except OSError as error:
            raise HistoryError(f"Could not write command history: {error}") from None
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink()
                except OSError:
                    pass
