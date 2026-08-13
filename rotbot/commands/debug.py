import tempfile

from rotbot.agents.invocation import prepare
from rotbot.agents.runner import build_ask_request
from rotbot.contexts import entities, loader, machines, people
from rotbot.contexts.config import ConfigError
from rotbot.contexts.creation import (
    ContextCreationError,
    build_context_develop_request
)
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
    ContextCreationError
)


def _display_request(request):
    print(render_ai_debug_plan(prepare(request)))
    return 0


def debug_ask(args):
    try:
        operation = build_ask_request(args)
        return _display_request(operation.request)
    except REQUEST_ERRORS as error:
        rot_say(str(error))
        return 2


def debug_context_develop(args):
    try:
        operation = build_context_develop_request(args, tempfile.gettempdir())
        return _display_request(operation.request)
    except REQUEST_ERRORS as error:
        rot_say(str(error))
        return 2


def debug_context_add(_args):
    rot_say(
        "Debug is not supported for context add because preparing that command "
        "requires context creation and binding. Use debug context develop for an "
        "existing project context."
    )
    return 2
