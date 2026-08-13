import os
from queue import Empty, Queue
from shutil import which
from signal import SIGILL
import subprocess
from threading import Thread
from time import perf_counter

from rotbot.agents.config import CODEX as codex_runner, OPENCODE as opencode_runner
from rotbot.contexts import entities, loader, machines, people
from rotbot.contexts.inspection import ContextInspectionError, inspect_current_context
from rotbot.contexts.prompt import build_ask_prompt, resolve_egress_context
from rotbot.ui.terminal import (
    rot_break,
    rot_output_end,
    rot_output_line,
    rot_output_start,
    rot_say,
    rot_status
)


resolve_prompt_context = resolve_egress_context


AGENTS = {
    "opencode": opencode_runner,
    "codex": codex_runner
}


def _select_agent(agent_name=None):
    requested_agent = (
        agent_name or os.environ.get("ROTBOT_AGENT", "")
    ).strip().lower()

    if requested_agent:
        agent = AGENTS.get(requested_agent)
        if agent is None:
            rot_say(
                f"Unknown ROTBOT_AGENT value: {requested_agent}\n"
                "Supported agents: opencode, codex"
            )
            return None
        if which(agent.EXECUTABLE) is None:
            rot_say(f"Configured AI agent is not available: {agent.NAME}")
            return None
        return agent

    for agent_name in ("opencode", "codex"):
        agent = AGENTS[agent_name]
        if which(agent.EXECUTABLE) is not None:
            return agent

    rot_say("No supported AI agent is available. Install OpenCode or Codex.")
    return None


def stream_agent(
    prompt,
    activity_message,
    working_directory=None,
    display_question=None,
    agent_name=None,
    timeout=None,
    isolated=False,
    display_output=True
):
    automatic_selection = (
        agent_name is None
        and not os.environ.get("ROTBOT_AGENT", "").strip()
    )
    agent = _select_agent(agent_name)
    if agent is None:
        return 127, "", 0
    rot_status(f"Using AI agent: {agent.NAME}.")
    rot_break()

    try:
        process = subprocess.Popen(
            agent.build_command(prompt, isolated=isolated),
            stdout=subprocess.PIPE,
            stderr=(
                subprocess.STDOUT
                if agent.MERGE_STDERR
                else subprocess.PIPE
            ),
            text=True,
            bufsize=1,
            cwd=working_directory
        )
    except FileNotFoundError:
        rot_say(f"AI agent is not available in PATH: {agent.NAME}")
        return 127, "", 0

    output_queue = Queue()
    output_lines = []
    error_lines = []

    def read_output():
        try:
            for line in process.stdout:
                output_queue.put(line)
        finally:
            output_queue.put(None)

    Thread(target=read_output, daemon=True).start()
    error_thread = None
    if process.stderr is not None:
        def read_errors():
            for line in process.stderr:
                error_lines.append(line)

        error_thread = Thread(target=read_errors, daemon=True)
        error_thread.start()

    started_at = perf_counter()
    output_started = False
    timed_out = False

    while True:
        try:
            line = output_queue.get(timeout=2)
        except Empty:
            if (
                timeout is not None
                and not timed_out
                and perf_counter() - started_at >= timeout
            ):
                process.kill()
                timed_out = True
            if not output_started:
                elapsed = round(perf_counter() - started_at)
                rot_status(f"{activity_message} {elapsed}s elapsed")
            continue

        if line is None:
            break

        output_lines.append(line)
        if not display_output:
            continue
        if line.strip():
            if not output_started:
                rot_output_start(display_question)
                output_started = True
            rot_output_line(line.rstrip("\r\n"))
        elif output_started:
            rot_output_line("")

    returncode = process.wait()
    if error_thread is not None:
        error_thread.join()
    if output_started:
        rot_output_end()

    if (
        automatic_selection
        and agent is opencode_runner
        and returncode == -SIGILL
        and which(codex_runner.EXECUTABLE) is not None
    ):
        return stream_agent(
            prompt,
            activity_message,
            working_directory,
            display_question,
            agent_name="codex",
            timeout=timeout,
            isolated=isolated,
            display_output=display_output
        )

    if timed_out:
        returncode = 124
        rot_say(f"{agent.NAME} timed out after {timeout} seconds.")
    elif returncode != 0:
        error_detail = "\n".join(
            line.rstrip()
            for line in error_lines[-8:]
            if line.strip()
        )
        message = f"{agent.NAME} failed with exit code {returncode}."
        if error_detail:
            message += f"\n{error_detail}"
        rot_say(message)

    elapsed = perf_counter() - started_at
    return returncode, "".join(output_lines), elapsed


def ask_agent(args):
    question = (
        " ".join(args.question)
        if isinstance(args.question, list)
        else args.question
    )
    agent = _select_agent(getattr(args, "agent", None))
    if agent is None:
        return 127
    try:
        inspected = inspect_current_context(bootstrap=False)
        context = resolve_prompt_context(inspected, agent.NAME)
        prompt = build_ask_prompt(context, question)
    except (
        ContextInspectionError,
        loader.ContextError,
        machines.MachineContextError,
        people.PersonContextError,
        entities.EntityContextError
    ) as error:
        rot_say(str(error))
        return 2

    rot_say("Let Rot think about that ...")
    returncode, output, elapsed = stream_agent(
        prompt,
        "Rot is still thinking...",
        working_directory=inspected.cwd,
        display_question=question,
        agent_name=getattr(args, "agent", None)
    )

    if returncode == 0 and not output.strip():
        rot_say("The AI agent returned no response.")
    if returncode == 0:
        rot_say(f"Response received in {elapsed:.1f}s.")

    return returncode
