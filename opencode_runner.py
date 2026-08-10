import subprocess

from gui import rot_say


def ask_opencode(args):
    rot_say("Asking OpenCode...")

    try:
        result = subprocess.run(
            ["opencode", "run", args.question],
            check=False
        )
    except FileNotFoundError:
        rot_say("OpenCode is not installed or is not available in PATH.")
        return 127

    return result.returncode
