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
