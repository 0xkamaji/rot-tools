import os
from pathlib import Path

from rotbot.contexts.config import ConfigError, get_context_binding
from rotbot.ui.terminal import rot_say


def source_path():
    configured = os.environ.get("SIGNALROT_REPO")
    if configured:
        return Path(configured).expanduser().resolve()
    try:
        configured = get_context_binding("signalrot").get("source_path")
    except ConfigError as error:
        rot_say(str(error))
        return None
    if configured:
        return Path(configured).expanduser().resolve()
    rot_say(
        "SignalRot source path is not configured.\n\n"
        "Run:\n  rot context bind signalrot /path/to/signalrot --as source"
    )
    return None


def production_path():
    configured = os.environ.get("SIGNALROT_WEB_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    try:
        configured = get_context_binding("signalrot").get("production_path")
    except ConfigError as error:
        rot_say(str(error))
        return None
    if configured:
        return Path(configured).expanduser().resolve()
    rot_say(
        "SignalRot production path is not configured.\n\n"
        "Run:\n  rot context bind signalrot /path/to/signalrot --as production"
    )
    return None
