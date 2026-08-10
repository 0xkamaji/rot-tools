from queue import Empty, Queue
import subprocess
from threading import Thread
from time import perf_counter

from gui import rot_continue, rot_say, rot_status


def stream_opencode(prompt, activity_message, working_directory=None):
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

    while True:
        try:
            line = output_queue.get(timeout=2)
        except Empty:
            elapsed = round(perf_counter() - started_at)
            rot_status(f"{activity_message} {elapsed}s elapsed")
            continue

        if line is None:
            break

        output_lines.append(line)
        if line.strip():
            rot_continue(line.rstrip())

    returncode = process.wait()
    elapsed = perf_counter() - started_at
    return returncode, "".join(output_lines), elapsed


def ask_opencode(args):
    rot_say("Let Rot think about that ...")
    returncode, output, _elapsed = stream_opencode(
        args.question,
        "Rot is still thinking..."
    )

    if returncode == 0 and not output.strip():
        rot_say("OpenCode returned no response.")

    return returncode
