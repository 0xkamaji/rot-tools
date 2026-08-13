from dataclasses import dataclass, field
from datetime import datetime
import uuid

from rotbot.agents.conversation import OpenCodeBackend, TextDelta
from rotbot.agents.invocation import (
    AIRequest,
    ConversationMessage,
    execute,
    prepare
)
from rotbot.session.conversations import ConversationStore


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
    backend: object
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
        capability_state=None, on_text=None
    ):
        if capability_state is not None:
            authority = capability_state.mode
        self._persist_start(inspected, cwd)
        backend_replaced = self.backend.prepare(authority, cwd) is True
        prior_messages = tuple(self.messages)
        user_turn = AIMessage(
            f"msg_{uuid.uuid4().hex}", "user", user_message,
            datetime.now().astimezone(), authority, "pending"
        )
        if self.store is not None:
            self.store.append_message(self.id, user_turn)
            self.store.update_metadata(self.id, **self._metadata(inspected, cwd))
        self.messages.append(user_turn)
        self.status = "thinking"
        assistant_parts = []
        try:
            request = self.build_request(
                user_message, inspected, cwd, authority, capability_state,
                messages=prior_messages, backend_replaced=backend_replaced
            )
            plan = prepare(request)

            def receive_text(text):
                if text:
                    assistant_parts.append(text)
                    self.status = "active"
                    if on_text is not None:
                        on_text(text)

            execute_plan = getattr(self.backend, "execute_plan", None)
            native_execute_plan = callable(
                getattr(type(self.backend), "execute_plan", None)
            )
            if native_execute_plan:
                execution = execute(
                    plan, executor=execute_plan, on_output=receive_text
                )
            else:
                execution = self._execute_compatible_backend(
                    plan, cwd, authority, receive_text
                )
            result = execution.value
            if not assistant_parts and result.response:
                assistant_parts.append(result.response)
                self.status = "active"
                if on_text is not None:
                    on_text(result.response)
        except BaseException as error:
            self.status = "active"
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
            partial = "".join(assistant_parts)
            if partial:
                assistant_turn = AIMessage(
                    f"msg_{uuid.uuid4().hex}", "assistant", partial,
                    datetime.now().astimezone(), authority, turn_status
                )
                self.messages.append(assistant_turn)
                if self.store is not None:
                    self.store.append_message(self.id, assistant_turn)
            raise
        self.status = "active"
        self._record_backend_state(result)
        self.messages[-1] = AIMessage(
            user_turn.id, user_turn.role, user_turn.content,
            user_turn.created_at, user_turn.authority, "complete"
        )
        if self.store is not None:
            self.store.update_message_status(self.id, user_turn.id, "complete")
        response = "".join(assistant_parts)
        if response:
            assistant_turn = AIMessage(
                f"msg_{uuid.uuid4().hex}", "assistant", response,
                datetime.now().astimezone(), authority
            )
            self.messages.append(assistant_turn)
            if self.store is not None:
                self.store.append_message(self.id, assistant_turn)
        self.context_fingerprint = plan.context_fingerprint
        if plan.context_sent:
            self.context_version += 1
        self.context_dirty = False
        if self.store is not None:
            self.store.update_metadata(self.id, **self._metadata(inspected, cwd))
        if response != result.response:
            result = type(result)(response, result.remote_state, result.model)
        return result

    def build_request(
        self, user_message, inspected, cwd, authority="TALK",
        capability_state=None, *, messages=None, backend_replaced=None
    ):
        if capability_state is not None:
            authority = capability_state.mode
        if backend_replaced is None:
            would_replace = getattr(self.backend, "would_replace", None)
            backend_replaced = (
                would_replace(authority, cwd) is True
                if callable(would_replace) else False
            )
        known_state = getattr(self.backend, "known_remote_state", lambda: ())()
        if not isinstance(known_state, (tuple, list)):
            known_state = tuple(self.remote_state)
        source_messages = tuple(self.messages) if messages is None else tuple(messages)
        return AIRequest(
            purpose="conversation",
            parent_command="interactive",
            task=user_message,
            working_directory=cwd,
            agent_name=self.backend.agent_name,
            inspected_context=inspected,
            capability_state=capability_state,
            conversation_id=self.id,
            conversation_messages=tuple(
                ConversationMessage(message.role, message.content, message.status)
                for message in source_messages
            ),
            provider_state=() if backend_replaced else tuple(known_state),
            previous_context_fingerprint=(
                None if backend_replaced else self.context_fingerprint
            ),
            context_dirty=self.context_dirty,
            authority=authority
        )

    def _execute_compatible_backend(self, plan, cwd, authority, on_text):
        def adapter(_plan, on_output=None):
            stream_generate = getattr(self.backend, "stream_generate", None)
            native_stream = callable(
                getattr(type(self.backend), "stream_generate", None)
            )
            if native_stream:
                stream = stream_generate(
                    _plan.provider_input, cwd, authority=authority
                )
                try:
                    while True:
                        try:
                            event = next(stream)
                        except StopIteration as completed:
                            return completed.value
                        if isinstance(event, TextDelta) and event.text and on_output:
                            on_output(event.text)
                finally:
                    stream.close()
            result = self.backend.generate(
                _plan.provider_input, cwd, authority=authority
            )
            if result.response and on_output:
                on_output(result.response)
            return result

        return execute(plan, executor=adapter, on_output=on_text)

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
