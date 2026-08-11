import os
from queue import Empty, Queue
from shutil import which
from signal import SIGILL
import subprocess
from threading import Thread
from time import perf_counter

from rotbot.agents.config import CODEX as codex_runner, OPENCODE as opencode_runner
from rotbot.ui.terminal import (
    rot_break,
    rot_output_end,
    rot_output_line,
    rot_output_start,
    rot_say,
    rot_status
)


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
    timeout=None
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
            agent.build_command(prompt),
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
            timeout=timeout
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
    rot_say("Let Rot think about that ...")
    returncode, output, elapsed = stream_agent(
        question,
        "Rot is still thinking...",
        display_question=question,
        agent_name=getattr(args, "agent", None)
    )

    if returncode == 0 and not output.strip():
        rot_say("The AI agent returned no response.")
    if returncode == 0:
        rot_say(f"Response received in {elapsed:.1f}s.")

    return returncode
