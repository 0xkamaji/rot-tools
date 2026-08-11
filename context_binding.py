from pathlib import Path

from context_matching import MatchError, match_contexts
from gui import rot_continue, rot_say
from rotbot_config import ConfigError, config_path, load_config, set_context_binding


def _confirm(message):
    rot_say(f"{message} [y/N]")
    try:
        answer = input("> ").strip().lower()
    except EOFError:
        answer = ""
    return answer in {"y", "yes"}


def context_bind(args):
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
