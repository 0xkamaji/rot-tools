import importlib
from pathlib import Path
import subprocess
import sys
import unittest

from rotbot.agents import ask_agent, stream_agent
from rotbot.contexts.matching import match_contexts


class PackageImportTests(unittest.TestCase):
    MODULES = (
        "rotbot",
        "rotbot.__main__",
        "rotbot.cli.parser",
        "rotbot.session.interactive",
        "rotbot.session.history",
        "rotbot.ui.interactive",
        "rotbot.ui.input",
        "rotbot.ui.terminal",
        "rotbot.agents.config",
        "rotbot.agents.runner",
        "rotbot.contexts.loader",
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
        candidates = match_contexts(repository, binding_type="source", caddy_paths=())
        strong = [candidate.name for candidate in candidates if candidate.strong]

        self.assertEqual(strong, ["rotbot"])


if __name__ == "__main__":
    unittest.main()
