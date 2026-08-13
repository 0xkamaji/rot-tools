from dataclasses import dataclass, field
from datetime import datetime
import os
from pathlib import Path

from rotbot.agents.conversation import ConversationError
from rotbot.cli.parser import parse_args
from rotbot.commands.git import PUSH_CANCELLED
from rotbot.contexts import entities, loader, machines, people
from rotbot.contexts.inspection import (
    ContextInspectionError,
    InspectedContext,
    inspect_current_context
)
from rotbot.session.ai import AIConversation
from rotbot.session.capabilities import (
    AssistantCapabilityPolicy,
    load_assistant_policy,
    resolve_capability_state,
    safe_policy
)
from rotbot.session.completion import CompletionProvider
from rotbot.session.conversations import ConversationStore, ConversationStoreError
from rotbot.session.history import CommandHistory, DEFAULT_DISPLAY_LIMIT, HistoryError
from rotbot.session.router import route_input
from rotbot.session.shell import run_shell
from rotbot.ui.interactive import (
    SessionHeader,
    clear_terminal,
    interactive_prompt,
    render_rot_response,
    StreamingRotResponse,
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
  talk                Use reasoning-only AI with no tool authority
  work                Grant scoped agentic authority for this project
  Tab                 Complete commands, contexts, executables, and paths
  exit / quit         End the Rot session

Rot commands can also be entered directly, including:

  ask "Explain this project"
  git status
  git pull
  git push
  context inspect
  context list
  context show NAME
  machine inspect
  ai sessions
  ai session show ROT_CONVERSATION_ID

Shell commands may be entered directly:

  ls -lah
  rg "pattern" .
  python --version

Natural language continues one AI conversation for this Rot session.

Rot commands run directly. Shell-shaped installed commands run locally.
Natural language goes to Rot.

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
    authority_mode: str = "TALK"
    work_project_id: str | None = None
    assistant_policy: AssistantCapabilityPolicy | None = None

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
        policy = (
            load_assistant_policy(context.assistant_id)
            if context.assistant_id else safe_policy("No assistant is resolved.")
        )
        return cls(
            datetime.now().astimezone(), cwd, context, history,
            assistant_policy=policy
        )

    def refresh_context(self):
        cwd = Path.cwd().resolve()
        context = inspect_current_context(cwd=cwd, bootstrap=False)
        self.cwd = cwd
        self.context = context
        self.assistant_policy = (
            load_assistant_policy(context.assistant_id)
            if context.assistant_id else safe_policy("No assistant is resolved.")
        )

    @property
    def capability_state(self):
        policy = self.assistant_policy or safe_policy(
            "No assistant capability policy is resolved."
        )
        return resolve_capability_state(
            self.context.assistant_id,
            policy,
            self.authority_mode,
            self.context.project_id,
            self.work_project_id
        )

    def change_directory(self, value):
        destination = Path(value).expanduser()
        try:
            resolved = destination.resolve(strict=True)
        except OSError:
            raise ValueError(f"Directory does not exist: {destination}") from None
        if not resolved.is_dir():
            raise ValueError(f"Not a directory: {resolved}")
        previous = self.cwd
        previous_project_id = self.context.project_id
        os.chdir(resolved)
        try:
            self.refresh_context()
        except BaseException:
            os.chdir(previous)
            raise
        if self.ai is not None:
            self.ai.mark_context_dirty()
        return (
            self.authority_mode == "WORK"
            and previous_project_id != self.context.project_id
        )

    @property
    def ai_status(self):
        if self.ai is None:
            return "idle"
        return self.ai.status if self.ai.status in {"idle", "thinking", "active"} else "active"

    def enable_work(self):
        if self.context.project_id is None:
            return False
        policy = self.assistant_policy or safe_policy(
            "No assistant capability policy is resolved."
        )
        state = resolve_capability_state(
            self.context.assistant_id,
            policy,
            "WORK",
            self.context.project_id,
            self.context.project_id
        )
        if state.mode != "WORK":
            return False
        self.authority_mode = state.mode
        self.work_project_id = state.work_project_id
        return True

    def enable_talk(self):
        self.authority_mode = "TALK"
        self.work_project_id = None

    def send_ai(self, message, header=None):
        if not message:
            rot_say("Usage: ? MESSAGE")
            return
        if self.ai is None:
            self.ai = AIConversation.create(store=ConversationStore())
        state = self.capability_state
        if not state.conversation:
            rot_say(state.denial_reason or "AI conversation is unavailable.")
            return
        self.authority_mode = state.mode
        self.work_project_id = state.work_project_id
        self.ai.status = "thinking"
        if header is not None:
            header.refresh(self)
        renderer = StreamingRotResponse(self)
        renderer.start()
        try:
            result = self.ai.send(
                message,
                self.context,
                self.cwd,
                authority=state.mode,
                capability_state=state,
                on_text=renderer.write
            )
        except (ConversationError, ConversationStoreError) as error:
            renderer.finish()
            rot_say(str(error))
            return
        except KeyboardInterrupt:
            self.ai.abort_current()
            renderer.finish(interrupted=True)
            if not renderer.started:
                rot_say("AI response interrupted.")
            return
        finally:
            renderer.finish()
            if self.ai.status == "thinking":
                self.ai.status = "active"
            if header is not None:
                header.refresh(self)
        if isinstance(result.response, str) and result.response and not renderer.started:
            render_rot_response(self, result.response)
        elif not result.response:
            rot_say("The AI backend returned no conversational response.")


def _run_rot_command(arguments, session=None):
    try:
        parsed = parse_args(arguments)
    except SystemExit:
        return None
    parsed.active_conversation_id = (
        session.ai.id if session is not None and session.ai is not None else None
    )
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
            if header is None:
                session.send_ai(route.value)
            else:
                session.send_ai(route.value, header=header)
        except (
            ContextInspectionError,
            loader.ContextError,
            entities.EntityContextError,
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
    if command == "work" and len(arguments) == 1:
        if session.enable_work():
            render_rot_response(
                session,
                f"Work mode enabled for {session.context.project}."
            )
        else:
            render_rot_response(
                session,
                "Work mode requires an active project and assistant policy. "
                "Talk mode remains enabled."
            )
        return True
    if command == "talk" and len(arguments) == 1:
        session.enable_talk()
        render_rot_response(session, "Talk mode enabled.")
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
            project_changed = session.change_directory(arguments[1])
        except (ValueError, ContextInspectionError) as error:
            rot_say(str(error))
            return True
        rot_say(
            f"Directory: {session.cwd}\n"
            f"Project: {session.context.project or 'none'}"
        )
        if project_changed:
            session.enable_talk()
            render_rot_response(
                session,
                "Work mode ended because the active project changed."
            )
        return True

    try:
        previous_project_id = session.context.project_id
        result = _run_rot_command(arguments, session)
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
    if result == 0 and changes_context:
        try:
            session.refresh_context()
        except ContextInspectionError as error:
            rot_say(f"Could not refresh session context.\n{error}")
        else:
            if session.ai is not None:
                session.ai.mark_context_dirty()
            if (
                session.authority_mode == "WORK"
                and previous_project_id != session.context.project_id
            ):
                session.enable_talk()
                render_rot_response(
                    session,
                    "Work mode ended because the active project changed."
                )
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
        input_backend.set_completion_provider(CompletionProvider(session))
    except Exception as error:
        rot_say(f"Warning: command history navigation is unavailable.\n{error}")
        input_backend = BasicInput()
    header.start(session)
    try:
        while True:
            header.refresh(session)
            try:
                line = input_backend.read(interactive_prompt(session))
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
            try:
                session.ai.close()
            except ConversationStoreError as error:
                rot_say(f"Warning: AI conversation could not be closed.\n{error}")
        try:
            session.command_history.save()
        except HistoryError as error:
            rot_say(f"Warning: command history could not be saved.\n{error}")
        header.stop()
