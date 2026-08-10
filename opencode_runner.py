from queue import Empty, Queue
import subprocess
from threading import Thread
from time import perf_counter

from gui import (
    rot_output_end,
    rot_output_line,
    rot_output_start,
    rot_say,
    rot_status
)


def stream_opencode(
    prompt,
    activity_message,
    working_directory=None,
    display_question=None
):
    try:
        process = subprocess.Popen(
            ["opencode", "run", prompt],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            cwd=working_directory
        )
    except FileNotFoundError:
        rot_say("OpenCode is not installed or is not available in PATH.")
        return 127, "", 0

    output_queue = Queue()
    output_lines = []

    def read_output():
        try:
            for line in process.stdout:
                output_queue.put(line)
        finally:
            output_queue.put(None)

    Thread(target=read_output, daemon=True).start()
    started_at = perf_counter()
    output_started = False

    while True:
        try:
            line = output_queue.get(timeout=2)
        except Empty:
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
    if output_started:
        rot_output_end()
    elapsed = perf_counter() - started_at
    return returncode, "".join(output_lines), elapsed


def ask_opencode(args):
    question = (
        " ".join(args.question)
        if isinstance(args.question, list)
        else args.question
    )
    rot_say("Let Rot think about that ...")
    returncode, output, _elapsed = stream_opencode(
        question,
        "Rot is still thinking...",
        display_question=question
    )

    if returncode == 0 and not output.strip():
        rot_say("OpenCode returned no response.")

    return returncode
