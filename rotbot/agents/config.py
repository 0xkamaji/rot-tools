class AgentConfig:
    def __init__(self, name, executable, merge_stderr, command_builder):
        self.NAME = name
        self.EXECUTABLE = executable
        self.MERGE_STDERR = merge_stderr
        self._command_builder = command_builder

    def build_command(self, prompt):
        return self._command_builder(prompt)


OPENCODE = AgentConfig(
    "OpenCode",
    "opencode",
    True,
    lambda prompt: ["opencode", "run", prompt]
)
CODEX = AgentConfig(
    "Codex",
    "codex",
    False,
    lambda prompt: [
        "codex",
        "exec",
        "--color",
        "never",
        "--skip-git-repo-check",
        prompt
    ]
)
AGENT_CHOICES = ("opencode", "codex")
