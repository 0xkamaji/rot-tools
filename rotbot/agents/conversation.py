import json
import os
from dataclasses import dataclass
from shutil import which
import subprocess

TALK_AGENT = "rotbot-talk"


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


@dataclass(frozen=True)
class TextDelta:
    text: str


class OpenCodeBackend:
    name = "OpenCode"

    def __init__(self):
        self.session_id = None
        self.directory = None
        self.current_process = None
        self.authority = None

    def _command(self, message, authority):
        if authority not in {"TALK", "WORK"}:
            raise ConversationError(f"Unsupported AI authority mode: {authority}")
        command = [
            "opencode", "run", "--format", "json", "--dir", str(self.directory)
        ]
        if authority == "TALK":
            command.extend(("--pure", "--agent", TALK_AGENT))
        if self.session_id is not None:
            command.extend(("--session", self.session_id))
        command.append(message)
        return command

    def _environment(self, authority):
        if authority not in {"TALK", "WORK"}:
            raise ConversationError(f"Unsupported AI authority mode: {authority}")
        environment = os.environ.copy()
        permissions = (
            {"*": "deny"}
            if authority != "WORK"
            else {
                "*": "allow",
                "external_directory": "deny",
                "question": "deny"
            }
        )
        environment["OPENCODE_PERMISSION"] = json.dumps(permissions)
        if authority == "TALK":
            try:
                inline_config = json.loads(
                    environment.get("OPENCODE_CONFIG_CONTENT", "{}")
                )
            except json.JSONDecodeError as error:
                raise ConversationError(
                    f"Invalid OPENCODE_CONFIG_CONTENT: {error}"
                ) from None
            agents = dict(inline_config.get("agent", {}))
            agents[TALK_AGENT] = {
                "description": "Rot TALK inference-only agent",
                "mode": "primary",
                "permission": {"*": "deny"}
            }
            inline_config["agent"] = agents
            environment["OPENCODE_CONFIG_CONTENT"] = json.dumps(inline_config)
        return environment

    def prepare(self, authority, cwd):
        if authority not in {"TALK", "WORK"}:
            raise ConversationError(f"Unsupported AI authority mode: {authority}")
        if self.directory is None:
            self.directory = cwd
            self.authority = authority
            return False
        if self.directory == cwd and self.authority == authority:
            return False
        self.session_id = None
        self.directory = cwd
        self.authority = authority
        return True

    def stream_generate(self, message, cwd, authority="TALK"):
        if which("opencode") is None:
            raise ConversationError("OpenCode is not installed or available in PATH.")
        environment = self._environment(authority)
        if self.directory is None:
            self.directory = cwd
            self.authority = authority
        try:
            process = subprocess.Popen(
                self._command(message, authority),
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
        response_parts = []
        errors = []
        model = None
        completed = False
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
                text = (
                    part.get("text")
                    if isinstance(part, dict) and part.get("type", "text") == "text"
                    else None
                )
                if not isinstance(text, str) or text == "":
                    continue
                response_parts.append(text)
                yield TextDelta(text)
                event_model = event.get("modelID")
                event_provider = event.get("providerID")
                if isinstance(event_model, str):
                    model = (
                        f"{event_provider}/{event_model}"
                        if isinstance(event_provider, str)
                        else event_model
                    )
            returncode = process.wait()
            completed = True
        except ConversationError:
            process.terminate()
            process.wait()
            raise
        except KeyboardInterrupt:
            process.terminate()
            process.wait()
            raise
        finally:
            if not completed and process.poll() is None:
                process.terminate()
                process.wait()
            self.current_process = None

        if returncode != 0:
            detail = "\n".join(errors[-8:])
            raise ConversationError(
                f"OpenCode failed with exit code {returncode}."
                + (f"\n{detail}" if detail else "")
            )
        if self.session_id is None:
            raise ConversationError("OpenCode did not return a session ID.")
        return BackendResult(
            response="".join(response_parts),
            remote_state=(BackendStateReference(
                layer="backend",
                provider="opencode",
                state_type="session",
                state_id=self.session_id,
                persistence="local_persistent"
            ),),
            model=model
        )

    def generate(self, message, cwd, authority="TALK"):
        stream = self.stream_generate(message, cwd, authority=authority)
        while True:
            try:
                next(stream)
            except StopIteration as completed:
                return completed.value

    def abort_current(self):
        if self.current_process is not None:
            self.current_process.terminate()

    def known_remote_state(self):
        if self.session_id is None:
            return ()
        return (BackendStateReference(
            layer="backend",
            provider="opencode",
            state_type="session",
            state_id=self.session_id,
            persistence="local_persistent"
        ),)

    def close(self):
        self.abort_current()


OpenCodeConversation = OpenCodeBackend
