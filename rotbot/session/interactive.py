from dataclasses import dataclass
from datetime import datetime
import os
from pathlib import Path
import shlex

from rotbot.cli.parser import parse_args
from rotbot.commands.git import PUSH_CANCELLED
from rotbot.contexts.inspection import (
    ContextInspectionError,
    InspectedContext,
    inspect_current_context
)
from rotbot.ui.interactive import (
    SessionHeader,
    clear_terminal,
    render_session_status
)
from rotbot.ui.terminal import rot_say


INTERACTIVE_HELP = """ROT INTERACTIVE COMMANDS

  help                Show this help
  status              Show current session status
  pwd                 Show current directory
  cd PATH             Change current directory
  clear               Clear and redraw the terminal
  exit / quit         End the Rot session

Rot commands can also be entered directly, including:

  ask "Explain this project"
  git status
  git pull
  git push
  wtf
  context inspect
  context list
  context show NAME
  machine inspect

Use the same command and options that would follow `rot` in the normal CLI.
Unknown input is never treated as an implicit shell or AI request."""


@dataclass
class RotSession:
    started_at: datetime
    cwd: Path
    context: InspectedContext

    @classmethod
    def start(cls):
        cwd = Path.cwd().resolve()
        context = inspect_current_context(cwd=cwd, bootstrap=False)
        return cls(datetime.now().astimezone(), cwd, context)

    def refresh_context(self):
        cwd = Path.cwd().resolve()
        context = inspect_current_context(cwd=cwd, bootstrap=False)
        self.cwd = cwd
        self.context = context

    def change_directory(self, value):
        destination = Path(value).expanduser()
        try:
            resolved = destination.resolve(strict=True)
        except OSError:
            raise ValueError(f"Directory does not exist: {destination}") from None
        if not resolved.is_dir():
            raise ValueError(f"Not a directory: {resolved}")
        previous = self.cwd
        os.chdir(resolved)
        try:
            self.refresh_context()
        except BaseException:
            os.chdir(previous)
            raise


def _run_rot_command(arguments):
    try:
        parsed = parse_args(arguments)
    except SystemExit:
        return
    result = parsed.func(parsed)
    if result is PUSH_CANCELLED:
        return


def evaluate_input(session, line, header=None):
    try:
        arguments = shlex.split(line)
    except ValueError as error:
        rot_say(f"Could not parse command: {error}")
        return True
    if not arguments:
        return True

    command = arguments[0].lower()
    if command in {"exit", "quit"} and len(arguments) == 1:
        return False
    if command == "help" and len(arguments) == 1:
        rot_say(INTERACTIVE_HELP)
        return True
    if command == "status" and len(arguments) == 1:
        rot_say(render_session_status(session))
        return True
    if command == "pwd" and len(arguments) == 1:
        rot_say(str(session.cwd))
        return True
    if command == "clear" and len(arguments) == 1:
        if header is None:
            clear_terminal()
        else:
            header.clear(session)
        return True
    if command == "cd":
        if len(arguments) != 2:
            rot_say("Usage: cd PATH")
            return True
        try:
            session.change_directory(arguments[1])
        except (ValueError, ContextInspectionError) as error:
            rot_say(str(error))
            return True
        rot_say(
            f"Directory: {session.cwd}\n"
            f"Project: {session.context.project or 'none'}"
        )
        return True

    try:
        _run_rot_command(arguments)
    except KeyboardInterrupt:
        rot_say("Command interrupted.")
    return True


def run_interactive():
    try:
        session = RotSession.start()
    except ContextInspectionError as error:
        rot_say(str(error))
        return 2

    header = SessionHeader()
    header.start(session)
    try:
        while True:
            header.refresh(session)
            try:
                line = input("rot> ")
            except EOFError:
                return 0
            except KeyboardInterrupt:
                print()
                continue
            if not evaluate_input(session, line, header=header):
                return 0
    finally:
        header.stop()
