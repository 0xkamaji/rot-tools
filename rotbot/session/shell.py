import os
from pathlib import Path
import shutil
import subprocess


SHELL_BUILTINS = {
    "alias", "command", "echo", "false", "kill", "printf", "read", "test",
    "time", "true", "type", "ulimit", "umask"
}


def _command_token(arguments):
    for argument in arguments:
        if "=" in argument and argument.split("=", 1)[0].isidentifier():
            continue
        return argument
    return None


def is_shell_command(arguments):
    command = _command_token(arguments)
    return bool(
        command
        and (
            command in SHELL_BUILTINS
            or shutil.which(command, path=os.environ.get("PATH")) is not None
        )
    )


def run_shell(command, cwd, environ=None):
    shell = os.environ.get("SHELL") or "/bin/sh"
    if not Path(shell).is_absolute() or not os.access(shell, os.X_OK):
        shell = "/bin/sh"
    try:
        return subprocess.run(
            command,
            shell=True,
            executable=shell,
            cwd=cwd,
            env=os.environ.copy() if environ is None else environ,
            check=False
        ).returncode
    except FileNotFoundError:
        return 127
    except OSError:
        return 1
