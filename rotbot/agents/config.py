class AgentConfig:
    def __init__(self, name, executable, merge_stderr, command_builder):
        self.NAME = name
        self.EXECUTABLE = executable
        self.MERGE_STDERR = merge_stderr
        self._command_builder = command_builder

    def build_command(self, prompt, isolated=False):
        return self._command_builder(prompt, isolated)


OPENCODE = AgentConfig(
    "OpenCode",
    "opencode",
    False,
    lambda prompt, isolated: [
        "opencode", "run", *(("--pure",) if isolated else ()), prompt
    ]
)
CODEX = AgentConfig(
    "Codex",
    "codex",
    False,
    lambda prompt, _isolated: [
        "codex",
        "exec",
        "--color",
        "never",
        "--skip-git-repo-check",
        prompt
    ]
)
AGENT_CHOICES = ("opencode", "codex")
