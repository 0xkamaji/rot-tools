from pathlib import Path

from rotbot.contexts import entities, loader, machines
from rotbot.contexts.config import ConfigError, config_path, load_config, set_context_binding
from rotbot.contexts.inspection import ContextInspectionError
from rotbot.contexts.matching import MatchError, match_contexts
from rotbot.session.state import SessionStateError
from rotbot.ui.terminal import rot_continue, rot_say


def _confirm(message):
    rot_say(f"{message} [y/N]")
    try:
        answer = input("> ").strip().lower()
    except EOFError:
        answer = ""
    return answer in {"y", "yes"}


SESSION_CONTEXT_TYPES = ("project", "user", "assistant", "machine")


def _choose(message, options):
    if not options:
        return None
    rot_say(
        message + "\n\n"
        + "\n".join(
            f"  {index}. {label}" for index, (label, _value) in enumerate(options, 1)
        )
        + f"\n  {len(options) + 1}. Exit"
    )
    while True:
        try:
            answer = input("> ").strip().lower()
        except EOFError:
            return None
        if answer in {"", "exit", "quit", "q", str(len(options) + 1)}:
            return None
        if answer.isdigit() and 1 <= int(answer) <= len(options):
            return options[int(answer) - 1][1]
        rot_say(f"Choose a number from 1 to {len(options) + 1}, or exit.")


def _available(context_type):
    if context_type == "project":
        return tuple(
            (context.name, context)
            for context in (loader.load_context(name) for name in loader.list_contexts())
        )
    if context_type == "user":
        contexts = entities.list_user_contexts()
    elif context_type == "assistant":
        contexts = entities.list_assistant_contexts()
    else:
        contexts = machines.list_machine_contexts()
    return tuple((context.name, context) for context in contexts)


def _load_session_context(context_type, reference):
    if context_type == "project":
        return loader.load_context_reference(reference)
    if context_type == "user":
        return entities.load_user_context(reference)
    if context_type == "assistant":
        return entities.load_assistant_context(reference)
    return machines.load_machine_context_reference(reference)


def _bind_session(args, context_type, reference=None):
    callback = getattr(args, "bind_session_context", None)
    if not callable(callback):
        rot_say("Session context binding is only available inside an interactive Rot session.")
        return 1
    try:
        if reference is None:
            options = _available(context_type)
            if not options:
                rot_say(f"No {context_type} contexts are available.")
                return 1
            context = _choose(f"Choose a {context_type} context to bind:", options)
            if context is None:
                rot_say("Context binding cancelled. The session was not changed.")
                return 0
        else:
            context = _load_session_context(context_type, reference)
    except (
        loader.ContextError, entities.EntityContextError, machines.MachineContextError
    ) as error:
        rot_say(str(error))
        return 1
    try:
        callback(context_type, context.name, context.id)
    except (ContextInspectionError, SessionStateError) as error:
        rot_say(f"Could not bind session context: {error}")
        return 1
    rot_say(f"Bound {context_type} context '{context.name}' to this session.")
    return 0


def _prompt_session_binding(args):
    context_type = _choose(
        "Which context type would you like to bind to this session?",
        tuple((name.title(), name) for name in SESSION_CONTEXT_TYPES)
    )
    if context_type is None:
        rot_say("Context binding cancelled. The session was not changed.")
        return 0
    return _bind_session(args, context_type)


def _bind_project_path(args):
    if args.second is None:
        name = None
        path = args.first or "."
    else:
        name = args.first
        path = args.second

    rot_say(f"Checking {Path(path).expanduser().resolve()}...")
    try:
        candidates = match_contexts(path, name, args.binding_type)
    except (MatchError, ConfigError) as error:
        rot_say(str(error))
        return 1

    strong = [candidate for candidate in candidates if candidate.strong]
    displayed = strong or candidates
    for candidate in displayed:
        status = "Strong match" if candidate.strong else "Not a strong match"
        rot_continue(f"{status}: {candidate.name} ({candidate.binding_type})")
        for evidence in candidate.evidence:
            marker = "+" if evidence.passed else "-"
            rot_continue(f"[{marker}] {evidence.message}")

    if not strong:
        rot_say("No strong context match found. No binding was saved.")
        return 1
    if len(strong) > 1:
        matches = ", ".join(
            f"{candidate.name} ({candidate.binding_type})"
            for candidate in strong
        )
        rot_say(f"Context match is ambiguous: {matches}. No binding was saved.")
        return 1

    selected = strong[0]
    path_key = f"{selected.binding_type}_path"
    target_config = config_path()
    try:
        load_config(target_config)
    except ConfigError as error:
        rot_say(str(error))
        return 1

    if not _confirm(f"Bind as {selected.name}.{path_key}?"):
        rot_say("Context binding cancelled. No configuration was changed.")
        return 0

    try:
        set_context_binding(
            selected.name,
            path_key,
            str(selected.path),
            target_config
        )
    except ConfigError as error:
        rot_say(str(error))
        return 1

    rot_say(f"Bound {selected.name}.{path_key} to:\n{selected.path}")
    return 0


def context_bind(args):
    if args.first is None:
        if callable(getattr(args, "bind_session_context", None)):
            return _prompt_session_binding(args)
        return _bind_project_path(args)
    if args.first in SESSION_CONTEXT_TYPES:
        if getattr(args, "binding_type", None) is not None:
            rot_say("--as is only supported for project path recognition.")
            return 1
        return _bind_session(args, args.first, args.second)
    return _bind_project_path(args)
