from dataclasses import dataclass, field
from datetime import datetime
import os
from pathlib import Path

from rotbot.agents.conversation import ConversationError
from rotbot.cli.parser import parse_args
from rotbot.commands.git import PUSH_CANCELLED
from rotbot.contexts import loader, machines, people
from rotbot.contexts.inspection import (
    ContextInspectionError,
    InspectedContext,
    inspect_current_context
)
from rotbot.session.ai import AIConversation
from rotbot.session.history import CommandHistory, DEFAULT_DISPLAY_LIMIT, HistoryError
from rotbot.session.router import route_input
from rotbot.session.shell import run_shell
from rotbot.ui.interactive import (
    SessionHeader,
    clear_terminal,
    render_session_status
)
from rotbot.ui.terminal import rot_say
from rotbot.ui.input import BasicInput, interactive_input


INTERACTIVE_HELP = """ROT INTERACTIVE COMMANDS

  help                Show this help
  status              Show current session status
  history [N]         Show recent commands (default: 20)
  pwd                 Show current directory
  cd PATH             Change current directory
  clear               Clear and redraw the terminal
  export NAME=VALUE   Set a session environment variable
  unset NAME          Remove a session environment variable
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

Shell commands may be entered directly:

  ls -lah
  rg "pattern" .
  python --version

Natural language continues one OpenCode conversation for this Rot session.

  ? MESSAGE           Force AI conversation
  ! COMMAND           Force shell execution

Use the same command and options that would follow `rot` in the normal CLI."""


@dataclass
class RotSession:
    started_at: datetime
    cwd: Path
    context: InspectedContext
    command_history: CommandHistory = field(default_factory=CommandHistory)
    ai: AIConversation | None = None

    @classmethod
    def start(cls):
        cwd = Path.cwd().resolve()
        context = inspect_current_context(cwd=cwd, bootstrap=False)
        history = CommandHistory()
        try:
            history.load()
        except HistoryError as error:
            history.persistence_enabled = False
            rot_say(f"Warning: command history could not be loaded.\n{error}")
        return cls(datetime.now().astimezone(), cwd, context, history)

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
        if self.ai is not None:
            self.ai.mark_context_dirty()

    def send_ai(self, message):
        if not message:
            rot_say("Usage: ? MESSAGE")
            return
        if self.ai is None:
            self.ai = AIConversation.create()
        try:
            self.ai.send(message, self.context, self.cwd)
        except ConversationError as error:
            rot_say(str(error))
            return
        except KeyboardInterrupt:
            self.ai.abort_current()
            rot_say("AI response interrupted.")
            return


def _run_rot_command(arguments):
    try:
        parsed = parse_args(arguments)
    except SystemExit:
        return None
    result = parsed.func(parsed)
    if result is PUSH_CANCELLED:
        return 0
    return result


def evaluate_input(session, line, header=None):
    route = route_input(line)
    if route.kind == "empty":
        return True

    if route.kind == "error":
        rot_say(route.value)
        return True
    if route.kind == "shell":
        try:
            returncode = run_shell(route.value, session.cwd)
        except KeyboardInterrupt:
            rot_say("Shell command interrupted.")
            return True
        if returncode != 0:
            rot_say(f"Shell command exited with status {returncode}.")
        return True
    if route.kind == "ai":
        try:
            session.send_ai(route.value)
        except (
            ContextInspectionError,
            loader.ContextError,
            machines.MachineContextError,
            people.PersonContextError
        ) as error:
            rot_say(str(error))
        return True

    arguments = route.value
    command = arguments[0].lower()
    if command in {"exit", "quit"} and len(arguments) == 1:
        return False
    if command == "help" and len(arguments) == 1:
        rot_say(INTERACTIVE_HELP)
        return True
    if command == "status" and len(arguments) == 1:
        rot_say(render_session_status(session))
        return True
    if command == "history":
        if len(arguments) > 2 or (
            len(arguments) == 2
            and (not arguments[1].isdigit() or int(arguments[1]) < 1)
        ):
            rot_say("Usage: history [positive count]")
            return True
        limit = int(arguments[1]) if len(arguments) == 2 else DEFAULT_DISPLAY_LIMIT
        entries = session.command_history.recent(limit)
        start = len(session.command_history.recent()) - len(entries) + 1
        rot_say(
            "COMMAND HISTORY\n---------------\n"
            + (
                "\n".join(
                    f"{index:>5}  {entry}"
                    for index, entry in enumerate(entries, start)
                )
                if entries else "(empty)"
            )
        )
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
    if command == "export":
        if len(arguments) != 2 or "=" not in arguments[1]:
            rot_say("Usage: export NAME=VALUE")
            return True
        name, value = arguments[1].split("=", 1)
        if not name.isidentifier():
            rot_say(f"Invalid environment variable name: {name}")
            return True
        os.environ[name] = value
        return True
    if command == "unset":
        if len(arguments) != 2 or not arguments[1].isidentifier():
            rot_say("Usage: unset NAME")
            return True
        os.environ.pop(arguments[1], None)
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
        result = _run_rot_command(arguments)
    except KeyboardInterrupt:
        rot_say("Command interrupted.")
        return True
    changes_context = (
        command == "machine"
        or (
            command == "context"
            and (
                len(arguments) == 1
                or arguments[1] in {"inspect", "bind", "add", "mod", "delete"}
            )
        )
    )
    if result == 0 and changes_context and session.ai is not None:
        try:
            session.refresh_context()
        except ContextInspectionError as error:
            rot_say(f"Could not refresh session context.\n{error}")
        else:
            session.ai.mark_context_dirty()
    return True


def run_interactive():
    try:
        session = RotSession.start()
    except ContextInspectionError as error:
        rot_say(str(error))
        return 2

    header = SessionHeader()
    input_backend = interactive_input()
    try:
        input_backend.prepare(session.command_history.recent())
    except Exception as error:
        rot_say(f"Warning: command history navigation is unavailable.\n{error}")
        input_backend = BasicInput()
    header.start(session)
    try:
        while True:
            header.refresh(session)
            try:
                line = input_backend.read("rot> ")
            except EOFError:
                return 0
            except KeyboardInterrupt:
                print()
                continue
            if session.command_history.add(line):
                try:
                    input_backend.record(line.strip())
                except Exception as error:
                    rot_say(
                        "Warning: command history navigation is unavailable.\n"
                        f"{error}"
                    )
                    input_backend = BasicInput()
            if not evaluate_input(session, line, header=header):
                return 0
    finally:
        if session.ai is not None:
            session.ai.close()
        try:
            session.command_history.save()
        except HistoryError as error:
            rot_say(f"Warning: command history could not be saved.\n{error}")
        header.stop()
