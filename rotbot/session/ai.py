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
from rotbot.session.conversations import ConversationStore


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
    id: str
    role: str
    content: str
    created_at: datetime
    authority: str
    status: str = "complete"


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
    store: ConversationStore | None = None
    persisted: bool = False
    closed_at: datetime | None = None

    @classmethod
    def create(cls, backend=None, store=None):
        return cls(
            id=f"rotconv_{uuid.uuid4().hex}",
            created_at=datetime.now().astimezone(),
            backend=OpenCodeBackend() if backend is None else backend,
            store=store
        )

    def _persist_start(self, inspected, cwd):
        if self.store is None or self.persisted:
            return
        self.store.create(self.id, self.created_at, inspected, cwd, self.backend.name)
        self.persisted = True

    def _metadata(self, inspected, cwd):
        return {
            "user_id": inspected.user_id or "",
            "assistant_id": inspected.assistant_id or "",
            "machine_id": inspected.machine_id or "",
            "project_id": inspected.project_id or "",
            "current_cwd": str(cwd),
            "backend": self.backend.name.lower(),
            "model": self.model or "",
            "context_fingerprint": self.context_fingerprint or "",
            "context_version": self.context_version,
            "remote_state": [
                {
                    "layer": reference.layer,
                    "provider": reference.provider,
                    "type": reference.state_type,
                    "id": reference.state_id,
                    "persistence": reference.persistence
                }
                for reference in self.remote_state
            ]
        }

    def mark_context_dirty(self):
        self.context_dirty = True

    def _compiled_input(self, inspected, user_message, capability_state=None):
        context = (
            resolve_prompt_context(
                inspected, self.backend.name, capability_state=capability_state
            )
            if capability_state is not None
            else resolve_prompt_context(inspected, self.backend.name)
        )
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

    def _record_known_backend_state(self):
        known_remote_state = getattr(self.backend, "known_remote_state", None)
        if not callable(known_remote_state):
            return
        references = known_remote_state()
        if not isinstance(references, (tuple, list)):
            return
        self._record_backend_state(type("KnownState", (), {
            "model": None,
            "remote_state": references
        })())

    def send(
        self, user_message, inspected, cwd, authority="TALK",
        capability_state=None
    ):
        if capability_state is not None:
            authority = capability_state.mode
        self._persist_start(inspected, cwd)
        backend_replaced = self.backend.prepare(authority, cwd) is True
        prior_messages = tuple(self.messages)
        if backend_replaced:
            self.context_fingerprint = None
        user_turn = AIMessage(
            f"msg_{uuid.uuid4().hex}", "user", user_message,
            datetime.now().astimezone(), authority, "pending"
        )
        if self.store is not None:
            self.store.append_message(self.id, user_turn)
            self.store.update_metadata(self.id, **self._metadata(inspected, cwd))
        self.messages.append(user_turn)
        if backend_replaced and prior_messages:
            transcript = _transcript_block(prior_messages)
        else:
            transcript = ""
        self.status = "thinking"
        try:
            prompt, fingerprint, context_updated = self._compiled_input(
                inspected, user_message, capability_state
            )
            if transcript:
                prompt = transcript + "\n\n" + prompt
            result = self.backend.generate(
                prompt,
                cwd,
                authority=authority
            )
        except BaseException as error:
            self.status = "idle"
            self._record_known_backend_state()
            turn_status = "aborted" if isinstance(error, KeyboardInterrupt) else "failed"
            self.messages[-1] = AIMessage(
                user_turn.id, user_turn.role, user_turn.content,
                user_turn.created_at, user_turn.authority, turn_status
            )
            if self.store is not None:
                try:
                    self.store.update_message_status(self.id, user_turn.id, turn_status)
                    self.store.update_metadata(
                        self.id, **self._metadata(inspected, cwd)
                    )
                except BaseException:
                    raise error
            raise
        self.status = "idle"
        self._record_backend_state(result)
        self.messages[-1] = AIMessage(
            user_turn.id, user_turn.role, user_turn.content,
            user_turn.created_at, user_turn.authority, "complete"
        )
        if self.store is not None:
            self.store.update_message_status(self.id, user_turn.id, "complete")
        if result.response:
            assistant_turn = AIMessage(
                f"msg_{uuid.uuid4().hex}", "assistant", result.response,
                datetime.now().astimezone(), authority
            )
            self.messages.append(assistant_turn)
            if self.store is not None:
                self.store.append_message(self.id, assistant_turn)
        self.context_fingerprint = fingerprint
        if context_updated:
            self.context_version += 1
        self.context_dirty = False
        if self.store is not None:
            self.store.update_metadata(self.id, **self._metadata(inspected, cwd))
        return result

    def abort_current(self):
        self.backend.abort_current()

    def close(self):
        try:
            if self.store is not None and self.persisted and self.closed_at is None:
                closed_at = datetime.now().astimezone()
                self.store.close(
                    self.id,
                    closed_at,
                    model=self.model or "",
                    context_fingerprint=self.context_fingerprint or "",
                    context_version=self.context_version,
                    remote_state=[
                        {
                            "layer": reference.layer,
                            "provider": reference.provider,
                            "type": reference.state_type,
                            "id": reference.state_id,
                            "persistence": reference.persistence
                        }
                        for reference in self.remote_state
                    ]
                )
                self.closed_at = closed_at
        finally:
            self.backend.close()
