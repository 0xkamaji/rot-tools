import os
from pathlib import Path


class PathConfigurationError(Exception):
    pass


def _xdg_root(variable, fallback, environ=None):
    environ = os.environ if environ is None else environ
    configured = environ.get(variable)
    root = Path(configured).expanduser() if configured else Path.home() / fallback
    if not root.is_absolute():
        raise PathConfigurationError(f"{variable} must be an absolute path.")
    return root


def data_root(environ=None):
    return _xdg_root("XDG_DATA_HOME", ".local/share", environ) / "rotbot"


def contexts_root(environ=None):
    environ = os.environ if environ is None else environ
    override = environ.get("ROTBOT_CONTEXT_ROOT")
    if override:
        root = Path(override).expanduser()
        if not root.is_absolute():
            raise PathConfigurationError("ROTBOT_CONTEXT_ROOT must be an absolute path.")
        return root
    return data_root(environ) / "contexts"


def config_root(environ=None):
    return _xdg_root("XDG_CONFIG_HOME", ".config", environ) / "rotbot"


def repository_root():
    return Path(__file__).resolve().parents[2]


def legacy_repository_context_root():
    return repository_root() / "context"


def builtin_root():
    return repository_root() / "builtin"


def builtin_assistants_root():
    return builtin_root() / "assistants"
