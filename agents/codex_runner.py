import os
from pathlib import Path
import re


NAME = "Codex"
EXECUTABLE = "codex"
MERGE_STDERR = False


def build_command(prompt):
    return [
        EXECUTABLE,
        "exec",
        "--color",
        "never",
        "--skip-git-repo-check",
        prompt
    ]


def get_model():
    config_root = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
    try:
        config = (config_root / "config.toml").read_text(encoding="utf-8")
    except OSError:
        return "provider default"

    match = re.search(r'^model\s*=\s*["\']([^"\']+)["\']', config, re.MULTILINE)
    return match.group(1) if match else "provider default"
