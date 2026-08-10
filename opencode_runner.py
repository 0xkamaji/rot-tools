import os
from queue import Empty, Queue
import subprocess
from threading import Thread
from time import perf_counter

from gui import rot_continue, rot_say, rot_status


def stream_opencode(prompt, activity_message):
    try:
        process = subprocess.Popen(
            ["opencode", "run", prompt],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1
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

    try:
        result = subprocess.run(
            ["opencode", "run", args.question],
            check=False
        )
    except FileNotFoundError:
        rot_say("OpenCode is not installed or is not available in PATH.")
        return 127

    return result.returncode


def directory_report(args):
    current_directory = os.getcwd()
    prompt = (
        "Inspect the current directory and produce a tailored, read-only WTF "
        "report. Do not modify any files. Examine the files and directories, "
        "then explain what this project or directory appears to be, the purpose "
        "of its important files and folders, how the pieces interact, likely "
        "entry points, and how to run, test, or build it. Include notable Git "
        "state, unfinished work, risks, and anything confusing or unknown. "
        "Prioritize useful specifics over generic advice. Format the response "
        "with clear sections beginning with 'WTF REPORT'.\n\n"
        f"Current directory: {current_directory}"
    )

    rot_say(
        "WTF inspection started.\n"
        f"Directory: {current_directory}\n"
        "Mode:      read-only"
    )
    returncode, output, elapsed = stream_opencode(
        prompt,
        "Rotbot is still investigating..."
    )

    if returncode != 0:
        rot_say(f"Directory inspection failed with exit code {returncode}.")
        return returncode

    if not output.strip():
        rot_say("OpenCode returned an empty directory report.")
        return 1

    rot_say(f"WTF report finished in {elapsed:.1f}s.")
    return 0
