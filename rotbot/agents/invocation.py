from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
from queue import Empty, Queue
from shutil import which
from signal import SIGILL
import subprocess
from threading import Thread
from time import perf_counter
import uuid

from rotbot.agents.config import CODEX, OPENCODE
from rotbot.contexts.prompt import (
    build_ask_prompt,
    build_context_refresh_prompt,
    resolve_egress_context
)


PROVIDERS = {"opencode": OPENCODE, "codex": CODEX}


@dataclass(frozen=True)
class ConversationMessage:
    role: str
    content: str
    status: str = "complete"


@dataclass(frozen=True)
class AIRequest:
    purpose: str
    parent_command: str
    task: str
    working_directory: Path | str | None = None
    agent_name: str | None = None
    inspected_context: object | None = None
    capability_state: object | None = None
    persistent_context: object | None = None
    context_material: str | None = None
    conversation_id: str | None = None
    conversation_messages: tuple[ConversationMessage, ...] = ()
    provider_state: tuple[object, ...] = ()
    previous_context_fingerprint: str | None = None
    context_dirty: bool = False
    authority: str | None = None
    output_contract: str | None = None
    retries: int = 0
    timeout: int | None = None
    isolated: bool = False


@dataclass(frozen=True)
class AIInvocationPlan:
    invocation_id: str
    purpose: str
    parent_command: str
    provider: object | None
    provider_name: str | None
    model: str | None
    working_directory: Path | str | None
    conversation_id: str | None
    provider_state: tuple[object, ...]
    available_persistent_context: object | None
    selected_persistent_context: object | None
    available_conversation: tuple[ConversationMessage, ...]
    selected_conversation: tuple[ConversationMessage, ...]
    task: str
    context_material: str | None
    provider_input: str
    output_contract: str | None
    retries: int
    timeout: int | None
    isolated: bool
    authority: str | None
    context_fingerprint: str | None = None
    context_sent: bool = False
    conversation_sent: bool = False
    preparation_error: str | None = None


@dataclass(frozen=True)
class AIResult:
    plan: AIInvocationPlan
    returncode: int
    output: str
    elapsed: float
    provider: str | None
    value: object = None
    validation_error: str | None = None
    attempts: int = 1

    @property
    def successful(self):
        return self.returncode == 0 and self.validation_error is None


def resolve_provider(agent_name=None):
    requested = (agent_name or os.environ.get("ROTBOT_AGENT", "")).strip().lower()
    if requested:
        provider = PROVIDERS.get(requested)
        if provider is None:
            return None, f"Unknown ROTBOT_AGENT value: {requested}"
        if which(provider.EXECUTABLE) is None:
            return None, f"Configured AI agent is not available: {provider.NAME}"
        return provider, None
    for name in ("opencode", "codex"):
        provider = PROVIDERS[name]
        if which(provider.EXECUTABLE) is not None:
            return provider, None
    return None, "No supported AI agent is available. Install OpenCode or Codex."


def _conversation_block(messages):
    completed = tuple(message for message in messages if message.status == "complete")
    if not completed:
        return ""
    lines = [
        "<rot_conversation_transcript>",
        "This is Rot's canonical transcript. Use it to restore continuity because "
        "the current provider session does not contain these turns."
    ]
    lines.extend(f"{message.role}: {message.content}" for message in completed)
    lines.append("</rot_conversation_transcript>")
    return "\n\n".join(lines)


def _persistent_context(request, provider_name):
    if request.persistent_context is not None:
        return request.persistent_context
    if request.inspected_context is None:
        return None
    if request.capability_state is None:
        return resolve_egress_context(request.inspected_context, provider_name)
    return resolve_egress_context(
        request.inspected_context,
        provider_name,
        capability_state=request.capability_state
    )


def prepare(request):
    provider, provider_error = resolve_provider(request.agent_name)
    provider_name = provider.NAME if provider is not None else None
    available_context = _persistent_context(request, provider_name or "unavailable")
    selected_context = available_context
    available_conversation = tuple(request.conversation_messages)
    selected_conversation = ()
    context_fingerprint = (
        hashlib.sha256(repr(available_context).encode("utf-8")).hexdigest()
        if available_context is not None else None
    )
    context_sent = False
    conversation_sent = False

    if request.purpose in {"ask", "conversation"}:
        session_available = bool(request.provider_state)
        if request.purpose == "conversation" and session_available:
            if (
                request.context_dirty
                or context_fingerprint != request.previous_context_fingerprint
            ):
                provider_input = build_context_refresh_prompt(
                    selected_context, request.task
                )
                context_sent = True
            else:
                provider_input = request.task
        else:
            provider_input = build_ask_prompt(selected_context, request.task)
            context_sent = True
            if available_conversation:
                selected_conversation = tuple(
                    message for message in available_conversation
                    if message.status == "complete"
                )
                transcript = _conversation_block(selected_conversation)
                if transcript:
                    provider_input = transcript + "\n\n" + provider_input
                    conversation_sent = True
    else:
        provider_input = request.task
        if request.context_material:
            provider_input += "\n\n" + request.context_material
        if request.output_contract:
            provider_input += "\n\nOUTPUT CONTRACT\n" + request.output_contract

    return AIInvocationPlan(
        invocation_id=uuid.uuid4().hex,
        purpose=request.purpose,
        parent_command=request.parent_command,
        provider=provider,
        provider_name=provider_name,
        model=None,
        working_directory=request.working_directory,
        conversation_id=request.conversation_id,
        provider_state=tuple(request.provider_state),
        available_persistent_context=available_context,
        selected_persistent_context=selected_context,
        available_conversation=available_conversation,
        selected_conversation=selected_conversation,
        task=request.task,
        context_material=request.context_material,
        provider_input=provider_input,
        output_contract=request.output_contract,
        retries=request.retries,
        timeout=request.timeout,
        isolated=request.isolated,
        authority=request.authority,
        context_fingerprint=context_fingerprint,
        context_sent=context_sent,
        conversation_sent=conversation_sent,
        preparation_error=provider_error
    )


def start_provider_process(command, *, cwd=None, env=None, merge_stderr=False):
    return subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT if merge_stderr else subprocess.PIPE,
        text=True,
        bufsize=1,
        cwd=cwd,
        env=env
    )


def _execute_attempt(plan, provider, on_event=None, on_output=None):
    if on_event:
        on_event("preparing")
    started = perf_counter()
    try:
        process = start_provider_process(
            provider.build_command(plan.provider_input, isolated=plan.isolated),
            cwd=plan.working_directory,
            merge_stderr=provider.MERGE_STDERR
        )
    except (FileNotFoundError, OSError) as error:
        if on_event:
            on_event("failed")
        return 127, "", 0, str(error)
    if on_event:
        on_event("started")
    queue = Queue()
    output = []
    errors = []

    def read_output():
        try:
            for line in process.stdout:
                queue.put(line)
        finally:
            queue.put(None)

    Thread(target=read_output, daemon=True).start()
    error_thread = None
    if process.stderr is not None:
        def read_errors():
            errors.extend(process.stderr)
        error_thread = Thread(target=read_errors, daemon=True)
        error_thread.start()
    timed_out = False
    streaming = False
    while True:
        try:
            line = queue.get(timeout=0.1)
        except Empty:
            if plan.timeout is not None and perf_counter() - started >= plan.timeout:
                process.kill()
                timed_out = True
            continue
        if line is None:
            break
        output.append(line)
        if not streaming and line.strip():
            streaming = True
            if on_event:
                on_event("streaming")
        if on_output is not None:
            on_output(line)
    returncode = process.wait()
    if error_thread is not None:
        error_thread.join()
    if timed_out:
        returncode = 124
    elapsed = perf_counter() - started
    error = "\n".join(line.rstrip() for line in errors[-8:] if line.strip())
    if on_event and returncode != 0:
        on_event("failed")
    return returncode, "".join(output), elapsed, error


def execute(
    plan, *, validator=None, on_event=None, on_output=None, executor=None
):
    if executor is not None:
        started = perf_counter()
        value = executor(plan, on_output=on_output)
        if isinstance(value, AIResult):
            return value
        return AIResult(
            plan=plan,
            returncode=0,
            output=getattr(value, "response", ""),
            elapsed=perf_counter() - started,
            provider=plan.provider_name,
            value=value
        )
    if plan.provider is None:
        if on_event:
            on_event("failed")
        return AIResult(
            plan, 127, "", 0, None,
            validation_error=plan.preparation_error
        )
    provider = plan.provider
    automatic = plan.provider_name == OPENCODE.NAME and not os.environ.get(
        "ROTBOT_AGENT", ""
    ).strip()
    output = ""
    elapsed = 0
    for attempt in range(1, plan.retries + 2):
        returncode, output, duration, error = _execute_attempt(
            plan, provider, on_event, on_output
        )
        elapsed += duration
        if automatic and provider is OPENCODE and returncode == -SIGILL and which(CODEX.EXECUTABLE):
            provider = CODEX
            returncode, output, duration, error = _execute_attempt(
                plan, provider, on_event, on_output
            )
            elapsed += duration
        if returncode != 0:
            detail = f"{provider.NAME} failed with exit code {returncode}."
            if error:
                detail += f"\n{error}"
            return AIResult(
                plan, returncode, output, elapsed, provider.NAME,
                validation_error=detail, attempts=attempt
            )
        if validator is None:
            if on_event:
                on_event("completed")
            return AIResult(plan, 0, output, elapsed, provider.NAME, attempts=attempt)
        if on_event:
            on_event("validating")
        try:
            value = validator(output)
        except Exception as error:
            if attempt <= plan.retries:
                if on_event:
                    on_event("retrying")
                continue
            if on_event:
                on_event("failed")
            return AIResult(
                plan, 0, output, elapsed, provider.NAME,
                validation_error=str(error), attempts=attempt
            )
        if on_event:
            on_event("completed")
        return AIResult(
            plan, 0, output, elapsed, provider.NAME,
            value=value, attempts=attempt
        )
    raise AssertionError("unreachable")


def invoke(request, *, validator=None, on_event=None, on_output=None):
    return execute(
        prepare(request),
        validator=validator,
        on_event=on_event,
        on_output=on_output
    )
