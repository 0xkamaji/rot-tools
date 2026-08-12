from dataclasses import dataclass, field
from datetime import datetime
import hashlib
import uuid

from rotbot.agents.conversation import OpenCodeBackend
from rotbot.contexts.prompt import (
    build_ask_prompt,
    build_context_refresh_prompt,
    resolve_prompt_context
)


def _transcript_block(messages):
    if not messages:
        return ""
    lines = [
        "<rot_conversation_transcript>",
        "This is Rot's canonical transcript from the current conversation. "
        "Use it only to restore conversational continuity after backend state "
        "was replaced."
    ]
    lines.extend(f"{message.role}: {message.content}" for message in messages)
    lines.append("</rot_conversation_transcript>")
    return "\n\n".join(lines)


@dataclass(frozen=True)
class AIMessage:
    role: str
    content: str
    created_at: datetime


@dataclass(frozen=True)
class RemoteStateReference:
    layer: str
    provider: str
    state_type: str
    state_id: str
    persistence: str


@dataclass
class AIConversation:
    id: str
    created_at: datetime
    backend: OpenCodeBackend
    messages: list[AIMessage] = field(default_factory=list)
    remote_state: list[RemoteStateReference] = field(default_factory=list)
    model: str | None = None
    context_fingerprint: str | None = None
    context_version: int = 0
    context_dirty: bool = False
    status: str = "idle"

    @classmethod
    def create(cls, backend=None):
        return cls(
            id=f"rotconv_{uuid.uuid4().hex}",
            created_at=datetime.now().astimezone(),
            backend=OpenCodeBackend() if backend is None else backend
        )

    def mark_context_dirty(self):
        self.context_dirty = True

    def _compiled_input(self, inspected, user_message):
        context = resolve_prompt_context(inspected, self.backend.name)
        fingerprint = hashlib.sha256(repr(context).encode("utf-8")).hexdigest()
        if self.context_fingerprint is None:
            return build_ask_prompt(context, user_message), fingerprint, True
        if self.context_dirty or fingerprint != self.context_fingerprint:
            return build_context_refresh_prompt(context, user_message), fingerprint, True
        return user_message, fingerprint, False

    def _record_backend_state(self, result):
        self.model = result.model or self.model
        for reference in result.remote_state:
            owned = RemoteStateReference(
                reference.layer,
                reference.provider,
                reference.state_type,
                reference.state_id,
                reference.persistence
            )
            if owned not in self.remote_state:
                self.remote_state.append(owned)

    def send(self, user_message, inspected, cwd, authority="TALK"):
        backend_replaced = self.backend.prepare(authority, cwd) is True
        prior_messages = tuple(self.messages)
        if backend_replaced:
            self.context_fingerprint = None
        self.messages.append(AIMessage("user", user_message, datetime.now().astimezone()))
        prompt, fingerprint, context_updated = self._compiled_input(
            inspected, user_message
        )
        if backend_replaced and prior_messages:
            prompt = _transcript_block(prior_messages) + "\n\n" + prompt
        self.status = "thinking"
        try:
            result = self.backend.generate(
                prompt,
                cwd,
                authority=authority
            )
        except BaseException:
            self.status = "idle"
            raise
        self._record_backend_state(result)
        if result.response:
            self.messages.append(
                AIMessage("assistant", result.response, datetime.now().astimezone())
            )
        self.context_fingerprint = fingerprint
        if context_updated:
            self.context_version += 1
        self.context_dirty = False
        self.status = "idle"
        return result

    def abort_current(self):
        self.backend.abort_current()

    def close(self):
        self.backend.close()
