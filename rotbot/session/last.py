from datetime import datetime
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile

from rotbot.contexts.paths import data_root


class LastResponseError(Exception):
    pass


def build_last_ask_message(last_text, instruction=None):
    if instruction:
        return (
            "FOLLOW-UP INSTRUCTION\n"
            f"{instruction}\n\n"
            "PREVIOUS RESPONSE\n"
            f"{last_text}"
        )
    return "PREVIOUS RESPONSE\n" + last_text


def edit_text(text, environ=None):
    environ = os.environ if environ is None else environ
    editor = environ.get("VISUAL") or environ.get("EDITOR")
    if not editor:
        raise LastResponseError("Set VISUAL or EDITOR before editing text.")
    try:
        command = shlex.split(editor)
    except ValueError as error:
        raise LastResponseError(f"Invalid editor command: {error}") from None
    if not command:
        raise LastResponseError("VISUAL or EDITOR does not name an editor.")
    path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", prefix="rotbot-edit-", suffix=".txt",
            delete=False
        ) as temporary:
            path = Path(temporary.name)
            temporary.write(text)
        if os.name != "nt":
            os.chmod(path, 0o600)
        try:
            completed = subprocess.run([*command, str(path)], check=False)
        except OSError as error:
            raise LastResponseError(f"Could not start editor: {error}") from None
        if completed.returncode != 0:
            raise LastResponseError(
                f"Editor exited with status {completed.returncode}; text was not changed."
            )
        try:
            return path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            raise LastResponseError(f"Could not read edited text: {error}") from None
    finally:
        if path is not None:
            try:
                path.unlink()
            except OSError:
                pass


def save_text(text, now=None, category="responses", filename_hint=None):
    if category not in {"responses", "debug"}:
        raise LastResponseError(f"Unsupported local text category: {category}")
    directory = data_root() / category
    try:
        directory.mkdir(parents=True, mode=0o700, exist_ok=True)
        if directory.is_symlink() or not directory.is_dir():
            raise LastResponseError(f"Invalid local save directory: {directory}")
        if os.name != "nt":
            os.chmod(directory, 0o700)
    except LastResponseError:
        raise
    except OSError as error:
        raise LastResponseError(f"Could not create local save directory: {error}") from None

    now = datetime.now().astimezone() if now is None else now
    default_hint = "ai-response" if category == "responses" else "debug-output"
    hint = filename_hint or default_hint
    hint = re.sub(r"[^a-z0-9]+", "-", hint.lower()).strip("-") or default_hint
    stem = now.strftime(f"%Y%m%d_%H%M%S_{hint}")
    for index in range(1, 10_000):
        suffix = "" if index == 1 else f"_{index}"
        path = directory / f"{stem}{suffix}.txt"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        try:
            descriptor = os.open(path, flags, 0o600)
        except FileExistsError:
            continue
        except OSError as error:
            raise LastResponseError(f"Could not save text: {error}") from None
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as destination:
                destination.write(text)
        except (OSError, UnicodeError) as error:
            try:
                path.unlink()
            except OSError:
                pass
            raise LastResponseError(f"Could not save text: {error}") from None
        if os.name != "nt":
            os.chmod(path, 0o600)
        return path
    raise LastResponseError("Could not choose a unique local filename.")


def learn_text(_text):
    raise LastResponseError(
        "Learning is not available in this Rot checkout; LAST was not changed."
    )
