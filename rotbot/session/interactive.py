from dataclasses import dataclass, field
from datetime import datetime
import os
from pathlib import Path

from rotbot.agents.conversation import ConversationError
from rotbot.cli.parser import parse_args
from rotbot.commands.git import PUSH_CANCELLED
from rotbot.contexts import entities, loader, machines, people
from rotbot.contexts.learning import LearningError, learn_text as store_learned_text
from rotbot.contexts.inspection import (
    ContextInspectionError,
    InspectedContext,
    inspect_current_context
)
from rotbot.contexts.config import ConfigError
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
from rotbot.session.last import (
    LastResponseError,
    build_last_ask_message,
    edit_text,
    save_text
)
from rotbot.session.state import SessionState, SessionStateError, SessionStateStore
from rotbot.agents.invocation import prepare
from rotbot.ui.debug import render_ai_debug_plan
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
  last show           Show the latest AI response
  last edit           Edit the latest AI response
  last save           Save the latest AI response locally
  last ask [MESSAGE]  Ask AI about the latest response
  last learn TARGET   Teach Rot from the latest response
  context learn TARGET [TEXT]   Store explicit local knowledge
  context show TARGET  Show local learned knowledge
  debug show          Show the latest debug output
  debug edit          Edit the latest debug output
  debug save          Save the latest debug output locally
  debug last ask [MESSAGE]   Inspect its next follow-up without sending
  Tab                 Complete commands, contexts, executables, and paths
  exit / quit         End the Rot session

Rot commands can also be entered directly, including:

  ask "Explain this project"
  git status
  git pull
  git push
  context show
  context show TARGET
  context learn TARGET [TEXT]
  context edit TARGET
  context list
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
class LastResponse:
    text: str
    source: str = "ai"
    edited: bool = False


@dataclass
class DebugResponse:
    text: str
    source: str
    edited: bool = False


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
    last_response: LastResponse | None = None
    debug_response: DebugResponse | None = None
    state_store: SessionStateStore = field(default_factory=SessionStateStore)

    @classmethod
    def start(cls, state_store=None):
        cwd = Path.cwd().resolve()
        context = inspect_current_context(cwd=cwd, bootstrap=True)
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
        session = cls(
            datetime.now().astimezone(), cwd, context, history,
            assistant_policy=policy,
            state_store=state_store if state_store is not None else SessionStateStore()
        )
        session.state_store.save(SessionState.from_inspected(context))
        return session

    def refresh_context(self):
        cwd = Path.cwd().resolve()
        previous = self.context
        observed = inspect_current_context(cwd=cwd, bootstrap=False)
        replacements = {}
        sources = observed.identification_sources
        for context_type in ("assistant", "user", "machine", "project"):
            if getattr(previous.identification_sources, context_type) != "session binding":
                continue
            replacements[context_type] = getattr(previous, context_type)
            replacements[f"{context_type}_id"] = getattr(
                previous, f"{context_type}_id"
            )
            sources = sources._replace(**{context_type: "session binding"})
        context = observed._replace(
            **replacements,
            identification_sources=sources
        )
        policy = self.assistant_policy
        if context.assistant_id != previous.assistant_id:
            policy = (
                load_assistant_policy(context.assistant_id)
                if context.assistant_id else safe_policy("No assistant is resolved.")
            )
        self.state_store.save(SessionState.from_inspected(context))
        self.cwd = cwd
        self.context = context
        self.assistant_policy = policy
        if self.ai is not None and context != previous:
            self.ai.mark_context_dirty()

    def bind_context(self, context_type, name, context_id):
        previous = self.context
        sources = previous.identification_sources._replace(
            **{context_type: "session binding"}
        )
        context = previous._replace(
            **{
                context_type: name,
                f"{context_type}_id": context_id,
                "identification_sources": sources
            }
        )
        policy = self.assistant_policy
        if context_type == "assistant" and context_id != previous.assistant_id:
            policy = load_assistant_policy(context_id)
        self.state_store.save(SessionState.from_inspected(context))
        self.context = context
        self.assistant_policy = policy
        if (
            self.authority_mode == "WORK"
            and context_type in {"assistant", "project"}
            and context_id != getattr(previous, f"{context_type}_id")
        ):
            self.enable_talk()
        if self.ai is not None:
            self.ai.mark_context_dirty()

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
            return None
        if self.ai is None:
            self.ai = AIConversation.create(store=ConversationStore())
        state = self.capability_state
        if not state.conversation:
            rot_say(state.denial_reason or "AI conversation is unavailable.")
            return None
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
        except (ConversationError, ConversationStoreError, ConfigError) as error:
            renderer.finish()
            rot_say(str(error))
            return None
        except KeyboardInterrupt:
            self.ai.abort_current()
            renderer.finish(interrupted=True)
            if not renderer.started:
                rot_say("AI response interrupted.")
            return None
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
            return None
        if isinstance(result.response, str) and result.response:
            self.last_response = LastResponse(result.response)
            return result.response
        return None

    def edit_last(self, header=None):
        if self.last_response is None:
            raise LastResponseError("No AI response is available in this session.")
        if header is not None:
            header.stop()
        try:
            edited = edit_text(self.last_response.text)
        finally:
            if header is not None:
                header.start(self)
        self.last_response.text = edited
        self.last_response.edited = True

    def edit_debug(self, header=None):
        if self.debug_response is None:
            raise LastResponseError("No debug output is available in this session.")
        if header is not None:
            header.stop()
        try:
            edited = edit_text(self.debug_response.text)
        finally:
            if header is not None:
                header.start(self)
        self.debug_response.text = edited
        self.debug_response.edited = True

    def store_debug(self, text, source):
        self.debug_response = DebugResponse(text, source)

    def ask_last(self, instruction=None, header=None):
        if self.last_response is None:
            raise LastResponseError("No AI response is available in this session.")
        previous = self.last_response.text
        message = build_last_ask_message(previous, instruction)
        return self.send_ai(message, header=header)

    def debug_last_ask(self, instruction=None):
        if self.last_response is None:
            raise LastResponseError("No AI response is available in this session.")
        if self.ai is None:
            raise LastResponseError(
                "No active AI conversation is available in this session."
            )
        state = self.capability_state
        if not state.conversation:
            raise LastResponseError(
                state.denial_reason or "AI conversation is unavailable."
            )
        message = build_last_ask_message(self.last_response.text, instruction)
        request = self.ai.build_request(
            message,
            self.context,
            self.cwd,
            authority=state.mode,
            capability_state=state
        )
        text = render_ai_debug_plan(prepare(request))
        print(text)
        self.store_debug(text, "debug-last-ask")


def _run_rot_command(arguments, session=None):
    try:
        parsed = parse_args(arguments)
    except SystemExit:
        return None
    parsed.active_conversation_id = (
        session.ai.id if session is not None and session.ai is not None else None
    )
    if session is not None:
        parsed.debug_sink = session.store_debug
        parsed.inspected_context = session.context
        parsed.bind_session_context = session.bind_context
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
    if command == "debug" and len(arguments) >= 3 and (
        arguments[1].lower(), arguments[2].lower()
    ) == ("last", "ask"):
        instruction = " ".join(arguments[3:]) or None
        try:
            session.debug_last_ask(instruction)
        except (LastResponseError, ConfigError) as error:
            rot_say(str(error))
        except KeyboardInterrupt:
            rot_say("Command interrupted.")
        return True
    if command == "debug" and len(arguments) == 2 and arguments[1].lower() in {
        "show", "edit", "save"
    }:
        action = arguments[1].lower()
        if action == "show":
            if session.debug_response is None:
                rot_say("No debug output is available in this session.")
            else:
                print(session.debug_response.text)
            return True
        if action == "edit":
            try:
                session.edit_debug(header=header)
            except LastResponseError as error:
                rot_say(str(error))
            return True
        if session.debug_response is None:
            rot_say("No debug output is available in this session.")
            return True
        try:
            path = save_text(
                session.debug_response.text,
                category="debug",
                filename_hint=session.debug_response.source
            )
        except LastResponseError as error:
            rot_say(str(error))
        else:
            rot_say(f"Saved DEBUG to:\n{path}")
        return True
    if command == "last":
        action = arguments[1].lower() if len(arguments) > 1 else None
        if action == "show" and len(arguments) == 2:
            if session.last_response is None:
                rot_say("No AI response is available in this session.")
            else:
                print(session.last_response.text)
            return True
        if action == "edit" and len(arguments) == 2:
            try:
                session.edit_last(header=header)
            except LastResponseError as error:
                rot_say(str(error))
            return True
        if action == "ask":
            instruction = " ".join(arguments[2:]) or None
            try:
                session.ask_last(instruction, header=header)
            except LastResponseError as error:
                rot_say(str(error))
            return True
        if action == "save" and len(arguments) == 2:
            if session.last_response is None:
                rot_say("No AI response is available in this session.")
                return True
            try:
                path = save_text(session.last_response.text, category="responses")
            except LastResponseError as error:
                rot_say(str(error))
            else:
                rot_say(f"Saved LAST to:\n{path}")
            return True
        if action == "learn" and len(arguments) >= 3:
            if session.last_response is None:
                rot_say("No AI response is available in this session.")
                return True
            target = arguments[2].lower()
            reference = (
                arguments[3]
                if target == "contact" and len(arguments) == 4 else None
            )
            if (target == "contact" and reference is None) or (
                target != "contact" and len(arguments) != 3
            ):
                rot_say("Usage: last learn TARGET [CONTACT]")
                return True
            try:
                store_learned_text(
                    target,
                    session.last_response.text,
                    inspected=session.context,
                    reference=reference
                )
            except LearningError as error:
                rot_say(str(error))
            return True
        rot_say("Usage: last show|edit|ask [MESSAGE]|save|learn TARGET [CONTACT]")
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
        previous_assistant_id = session.context.assistant_id
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
                or arguments[1] in {"bind", "add", "mod", "delete"}
            )
        )
    )
    if result == 0 and changes_context:
        try:
            session.refresh_context()
        except (ContextInspectionError, SessionStateError) as error:
            rot_say(f"Could not refresh session context.\n{error}")
        else:
            if (
                session.authority_mode == "WORK"
                and (
                    previous_project_id != session.context.project_id
                    or previous_assistant_id != session.context.assistant_id
                )
            ):
                session.enable_talk()
                render_rot_response(
                    session,
                    "Work mode ended because the active project or assistant changed."
                )
    return True


def run_interactive():
    try:
        session = RotSession.start()
    except (ContextInspectionError, SessionStateError) as error:
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
