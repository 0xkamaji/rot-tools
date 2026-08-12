import importlib
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from rotbot.agents import ask_agent, stream_agent
from rotbot.contexts import loader
from rotbot.contexts.matching import match_contexts


class PackageImportTests(unittest.TestCase):
    MODULES = (
        "rotbot",
        "rotbot.__main__",
        "rotbot.cli.parser",
        "rotbot.session.interactive",
        "rotbot.session.ai",
        "rotbot.session.conversations",
        "rotbot.session.capabilities",
        "rotbot.session.completion",
        "rotbot.session.history",
        "rotbot.session.router",
        "rotbot.session.shell",
        "rotbot.ui.interactive",
        "rotbot.ui.input",
        "rotbot.ui.terminal",
        "rotbot.agents.config",
        "rotbot.agents.conversation",
        "rotbot.agents.runner",
        "rotbot.contexts.loader",
        "rotbot.contexts.entities",
        "rotbot.contexts.inspection",
        "rotbot.contexts.matching",
        "rotbot.contexts.menu",
        "rotbot.contexts.modification",
        "rotbot.contexts.binding",
        "rotbot.contexts.creation",
        "rotbot.contexts.config",
        "rotbot.contexts.deletion",
        "rotbot.contexts.machines",
        "rotbot.contexts.people",
        "rotbot.commands.git",
        "rotbot.commands.ai",
        "rotbot.commands.machine",
        "rotbot.commands.wtf",
        "rotbot.integrations.signalrot.commands",
        "rotbot.integrations.signalrot.context",
        "rotbot.integrations.signalrot.paths"
    )

    def test_major_package_modules_import(self):
        for module_name in self.MODULES:
            with self.subTest(module=module_name):
                self.assertIsNotNone(importlib.import_module(module_name))

        self.assertTrue(callable(ask_agent))
        self.assertTrue(callable(stream_agent))

    def test_python_module_entry_point_renders_help(self):
        repository = Path(__file__).resolve().parent.parent
        result = subprocess.run(
            [sys.executable, "-m", "rotbot", "--help"],
            cwd=repository,
            capture_output=True,
            text=True,
            check=False
        )

        self.assertEqual(result.returncode, 0)
        self.assertIn("usage: rotbot", result.stdout)
        self.assertIn("Signal Rot commands", result.stdout)

    def test_rotbot_repository_matches_only_rotbot_context(self):
        repository = Path(__file__).resolve().parent.parent
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "contexts"
            project = root / "projects" / "rotbot"
            local = project / "local"
            local.mkdir(parents=True)
            (project / "metadata.toml").write_text(
                loader.render_project_metadata("rotbot"), encoding="utf-8"
            )
            (local / "identity.md").write_text("identity", encoding="utf-8")
            (local / "state.md").write_text("state", encoding="utf-8")
            (local / "match.md").write_text(
                "# Match\n\n## Source\n\nGit remotes:\n\n"
                "- github.com/0xkamaji/rotbot\n\nRequired paths:\n\n"
                "- rotbot/\n- tests/\n",
                encoding="utf-8"
            )
            with patch.object(loader, "CONTEXT_ROOT", root):
                candidates = match_contexts(
                    repository, binding_type="source", caddy_paths=()
                )
        strong = [candidate.name for candidate in candidates if candidate.strong]

        self.assertEqual(strong, ["rotbot"])


if __name__ == "__main__":
    unittest.main()
