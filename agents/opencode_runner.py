NAME = "OpenCode"
EXECUTABLE = "opencode"
MERGE_STDERR = True


def build_command(prompt):
    return [EXECUTABLE, "run", prompt]
