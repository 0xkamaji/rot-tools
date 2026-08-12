import json
import os
from dataclasses import dataclass
from shutil import which
import subprocess

from rotbot.ui.terminal import (
    rot_output_end,
    rot_output_line,
    rot_output_start,
    rot_say
)


class ConversationError(Exception):
    pass


@dataclass(frozen=True)
class BackendStateReference:
    layer: str
    provider: str
    state_type: str
    state_id: str
    persistence: str


@dataclass(frozen=True)
class BackendResult:
    response: str
    remote_state: tuple[BackendStateReference, ...]
    model: str | None = None


class OpenCodeBackend:
    name = "OpenCode"

    def __init__(self):
        self.session_id = None
        self.directory = None
        self.current_process = None

    def _command(self, message):
        command = [
            "opencode", "run", "--format", "json", "--dir", str(self.directory)
        ]
        if self.session_id is not None:
            command.extend(("--session", self.session_id))
        command.append(message)
        return command

    def generate(self, message, cwd, display_question=None):
        if which("opencode") is None:
            raise ConversationError("OpenCode is not installed or available in PATH.")
        environment = os.environ.copy()
        environment["OPENCODE_PERMISSION"] = json.dumps({
            "bash": "deny",
            "edit": "deny"
        })
        if self.directory is None:
            self.directory = cwd
        try:
            process = subprocess.Popen(
                self._command(message),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                cwd=self.directory,
                env=environment
            )
        except (FileNotFoundError, OSError) as error:
            raise ConversationError(f"Could not start OpenCode: {error}") from None

        self.current_process = process
        output_started = False
        response_parts = []
        errors = []
        model = None
        try:
            for raw_line in process.stdout:
                try:
                    event = json.loads(raw_line)
                except json.JSONDecodeError:
                    errors.append(raw_line.rstrip())
                    continue
                session_id = event.get("sessionID")
                if isinstance(session_id, str):
                    if self.session_id is not None and session_id != self.session_id:
                        raise ConversationError("OpenCode returned an unexpected session ID.")
                    self.session_id = session_id
                if event.get("type") == "error":
                    errors.append(str(event.get("error", "OpenCode request failed.")))
                    continue
                if event.get("type") != "text":
                    continue
                part = event.get("part", {})
                text = part.get("text") if isinstance(part, dict) else None
                if not isinstance(text, str) or not text.strip():
                    continue
                response_parts.append(text.strip())
                event_model = event.get("modelID")
                event_provider = event.get("providerID")
                if isinstance(event_model, str):
                    model = (
                        f"{event_provider}/{event_model}"
                        if isinstance(event_provider, str)
                        else event_model
                    )
                if not output_started:
                    rot_output_start(display_question)
                    output_started = True
                for line in text.rstrip().splitlines():
                    rot_output_line(line)
            returncode = process.wait()
        except ConversationError:
            process.terminate()
            process.wait()
            raise
        except KeyboardInterrupt:
            process.terminate()
            process.wait()
            raise
        finally:
            self.current_process = None
            if output_started:
                rot_output_end()

        if returncode != 0:
            detail = "\n".join(errors[-8:])
            raise ConversationError(
                f"OpenCode failed with exit code {returncode}."
                + (f"\n{detail}" if detail else "")
            )
        if self.session_id is None:
            raise ConversationError("OpenCode did not return a session ID.")
        if not output_started:
            rot_say("OpenCode returned no conversational response.")
        return BackendResult(
            response="\n\n".join(response_parts),
            remote_state=(BackendStateReference(
                layer="backend",
                provider="opencode",
                state_type="session",
                state_id=self.session_id,
                persistence="local_persistent"
            ),),
            model=model
        )

    def abort_current(self):
        if self.current_process is not None:
            self.current_process.terminate()

    def close(self):
        self.abort_current()


OpenCodeConversation = OpenCodeBackend
