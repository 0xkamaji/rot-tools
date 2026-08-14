from rotbot.agents.invocation import prepare
from rotbot.agents.runner import build_ask_request
from rotbot.contexts import entities, loader, machines, people
from rotbot.contexts.config import ConfigError
from rotbot.contexts.inspection import ContextInspectionError
from rotbot.contexts.matching import MatchError
from rotbot.ui.debug import render_ai_debug_plan
from rotbot.ui.terminal import rot_say


REQUEST_ERRORS = (
    ContextInspectionError,
    loader.ContextError,
    machines.MachineContextError,
    people.PersonContextError,
    entities.EntityContextError,
    MatchError,
    ConfigError,
)


def _display_debug(args, text, source):
    print(text)
    sink = getattr(args, "debug_sink", None)
    if sink is not None:
        sink(text, source)
    return 0


def _display_request(args, request, source):
    return _display_debug(args, render_ai_debug_plan(prepare(request)), source)


def debug_ask(args):
    try:
        operation = build_ask_request(args)
        return _display_request(args, operation.request, "debug-ask")
    except REQUEST_ERRORS as error:
        rot_say(str(error))
        return 2


def debug_last_ask(_args):
    rot_say("debug last ask is available only inside an interactive Rot session.")
    return 2


def debug_session_register(args):
    action = getattr(args, "debug_command", "operation")
    rot_say(
        f"debug {action} is available only inside an interactive Rot session."
    )
    return 2
