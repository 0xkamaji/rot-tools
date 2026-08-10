import os
from pathlib import Path
import re


NAME = "OpenCode"
EXECUTABLE = "opencode"
MERGE_STDERR = True


def build_command(prompt):
    return [EXECUTABLE, "run", prompt]


def get_model():
    configured_path = os.environ.get("OPENCODE_CONFIG")
    if configured_path:
        config_paths = (Path(configured_path).expanduser(),)
    else:
        config_root = Path(
            os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")
        )
        config_paths = (
            config_root / "opencode" / "opencode.json",
            config_root / "opencode" / "opencode.jsonc"
        )

    for config_path in config_paths:
        try:
            config = config_path.read_text(encoding="utf-8")
        except OSError:
            continue

        match = re.search(r'"model"\s*:\s*"([^"]+)"', config)
        if match:
            return match.group(1)

    return "provider default"
