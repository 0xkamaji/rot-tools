from dataclasses import dataclass, field
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


PROVIDERS = {"opencode": OPENCODE, "codex": CODEX}


@dataclass(frozen=True)
class AIInvocation:
    purpose: str
    parent_command: str
    prompt: str
    working_directory: Path | str | None = None
    agent_name: str | None = None
    timeout: int | None = None
    structured_output: str | None = None
    retries: int = 0
    conversation: bool = False
    isolated: bool = False
    display_output: bool = True
    invocation_id: str = field(default_factory=lambda: uuid.uuid4().hex)


@dataclass(frozen=True)
class AIInvocationResult:
    invocation: AIInvocation
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


def _execute(invocation, provider, on_event=None, on_output=None):
    if on_event:
        on_event("preparing")
    started = perf_counter()
    try:
        process = start_provider_process(
            provider.build_command(invocation.prompt, isolated=invocation.isolated),
            cwd=invocation.working_directory,
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
            if invocation.timeout is not None and perf_counter() - started >= invocation.timeout:
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


def invoke(invocation, *, validator=None, on_event=None, on_output=None):
    provider, provider_error = resolve_provider(invocation.agent_name)
    if provider is None:
        if on_event:
            on_event("failed")
        return AIInvocationResult(
            invocation, 127, "", 0, None,
            validation_error=provider_error
        )
    automatic = invocation.agent_name is None and not os.environ.get("ROTBOT_AGENT", "").strip()
    validation_error = None
    output = ""
    elapsed = 0
    for attempt in range(1, invocation.retries + 2):
        returncode, output, duration, error = _execute(
            invocation, provider, on_event, on_output
        )
        elapsed += duration
        if automatic and provider is OPENCODE and returncode == -SIGILL and which(CODEX.EXECUTABLE):
            provider = CODEX
            returncode, output, duration, error = _execute(
                invocation, provider, on_event, on_output
            )
            elapsed += duration
        if returncode != 0:
            detail = f"{provider.NAME} failed with exit code {returncode}."
            if error:
                detail += f"\n{error}"
            return AIInvocationResult(
                invocation, returncode, output, elapsed, provider.NAME,
                validation_error=detail, attempts=attempt
            )
        if validator is None:
            if on_event:
                on_event("completed")
            return AIInvocationResult(
                invocation, 0, output, elapsed, provider.NAME, attempts=attempt
            )
        if on_event:
            on_event("validating")
        try:
            value = validator(output)
        except Exception as error:
            validation_error = str(error)
            if attempt <= invocation.retries:
                if on_event:
                    on_event("retrying")
                continue
            if on_event:
                on_event("failed")
            return AIInvocationResult(
                invocation, 0, output, elapsed, provider.NAME,
                validation_error=validation_error, attempts=attempt
            )
        if on_event:
            on_event("completed")
        return AIInvocationResult(
            invocation, 0, output, elapsed, provider.NAME,
            value=value, attempts=attempt
        )
    raise AssertionError("unreachable")
