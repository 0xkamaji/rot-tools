import os
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
    requested_target = getattr(args, "target", None)
    target = (
        os.path.abspath(os.path.expanduser(requested_target))
        if requested_target
        else current_directory
    )

    if not os.path.exists(target):
        rot_say(f"Cannot inspect a path that does not exist:\n{target}")
        return 1

    target_type = "directory" if os.path.isdir(target) else "file"
    inspection_directory = target if target_type == "directory" else os.path.dirname(target)
    prompt = (
        f"Inspect the specified {target_type} and produce a tailored, read-only "
        "WTF report. Do not modify any files. Focus specifically on the target "
        "while inspecting nearby project context when needed. Explain what it "
        "is, its purpose, how it interacts with related files, likely entry "
        "points, and how to run, test, or build the relevant code. Include "
        "notable Git state, unfinished work, risks, and anything confusing or "
        "unknown. Prioritize useful specifics over generic advice. Format the "
        "response with clear sections beginning with 'WTF REPORT'.\n\n"
        f"Requested target: {target}\n"
        f"Target type: {target_type}\n"
        f"Invocation directory: {current_directory}"
    )

    rot_say(
        "WTF inspection started.\n"
        f"Target: {target}\n"
        f"Type:   {target_type}\n"
        "Mode:   read-only"
    )
    returncode, output, elapsed = stream_opencode(
        prompt,
        "Rotbot is still investigating...",
        inspection_directory
    )

    if returncode != 0:
        rot_say(f"Directory inspection failed with exit code {returncode}.")
        return returncode

    if not output.strip():
        rot_say("OpenCode returned an empty directory report.")
        return 1

    rot_say(f"WTF report finished in {elapsed:.1f}s.")
    return 0
