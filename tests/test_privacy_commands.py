import os
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import Mock, patch

from rotbot.cli import parser as command_parser
from rotbot.commands import ai, privacy
from rotbot.contexts import loader
from rotbot.contexts.inspection import IdentificationSources, InspectedContext
from rotbot.contexts.prompt import PromptContext


class PrivacyCommandTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_privacy_inspect_lists_filenames_without_secret_contents(self):
        context = self.root / "rotbot" / "contexts" / "users" / "kamaji"
        (context / "private").mkdir(parents=True)
        (context / "general").mkdir()
        (context / "private" / "secrets.md").write_text(
            "do-not-print-this-secret", encoding="utf-8"
        )
        (context / "general" / "profile.md").write_text(
            "general-content-must-not-be-read", encoding="utf-8"
        )

        with patch.object(
            loader, "CONTEXT_ROOT", self.root / "rotbot" / "contexts"
        ), patch.object(privacy, "rot_say") as say:
            result = privacy.privacy_inspect(Mock())

        self.assertEqual(result, 0)
        rendered = say.call_args.args[0]
        self.assertIn("secrets.md", rendered)
        self.assertIn("profile.md", rendered)
        self.assertNotIn("do-not-print-this-secret", rendered)
        self.assertNotIn("general-content-must-not-be-read", rendered)
        self.assertIn("Machine-local config: excluded", rendered)
        for category in ("USERS", "ASSISTANTS", "MACHINES", "PROJECTS", "CONTACTS"):
            self.assertIn(category, rendered)

    def test_ai_context_preview_uses_egress_context_without_backend(self):
        inspected = InspectedContext(
            "rot", "assistant-id", "kamaji", "user-id", "laptop", "machine-id",
            "rotbot", "project-id", self.root,
            IdentificationSources("local", "local", "local", "source"), ()
        )
        resolved = PromptContext(None, None, None, None, str(self.root), "preview")
        with patch.object(
            ai, "inspect_current_context", return_value=inspected
        ) as inspect, patch.object(
            ai.prompt, "resolve_egress_context", return_value=resolved
        ) as resolve, patch.object(
            ai.prompt, "_context_blocks", return_value=["GENERAL EGRESS PREVIEW"]
        ), patch.object(ai, "rot_say") as say, patch(
            "rotbot.agents.runner.ask_agent"
        ) as backend, patch.object(socket, "create_connection") as network:
            result = ai.ai_context_preview(Mock())

        self.assertEqual(result, 0)
        inspect.assert_called_once_with(bootstrap=False)
        resolve.assert_called_once_with(inspected, "preview")
        backend.assert_not_called()
        network.assert_not_called()
        rendered = say.call_args.args[0]
        self.assertIn("ROT AI CONTEXT PREVIEW", rendered)
        self.assertIn("GENERAL EGRESS PREVIEW", rendered)
        self.assertIn("assistants/rot/private/", rendered)
        self.assertIn("users/kamaji/private/", rendered)
        self.assertIn("machines/laptop/private/", rendered)
        self.assertIn("projects/rotbot/private/", rendered)

    def test_parser_routes_and_scoped_help(self):
        self.assertIs(command_parser.parse_args(["ai", "context", "preview"]).func, ai.ai_context_preview)
        self.assertIs(command_parser.parse_args(["privacy", "inspect"]).func, privacy.privacy_inspect)
        for argv, expected in (
            (["ai", "context"], "preview"),
            (["privacy"], "inspect")
        ):
            with self.subTest(argv=argv), patch.object(
                command_parser, "rot_say"
            ) as say:
                args = command_parser.parse_args(argv)
                self.assertEqual(args.func(args), 0)
            self.assertIn(expected, say.call_args.args[0])

    def test_commands_report_errors(self):
        with patch.object(
            ai, "inspect_current_context", side_effect=ai.ContextInspectionError("bad context")
        ), patch.object(ai, "rot_say") as say:
            self.assertEqual(ai.ai_context_preview(Mock()), 2)
            self.assertIn("bad context", say.call_args.args[0])
        invalid = self.root / "invalid"
        invalid.write_text("not a directory", encoding="utf-8")
        with patch.object(loader, "CONTEXT_ROOT", invalid), patch.object(
            privacy, "rot_say"
        ) as say:
            self.assertEqual(privacy.privacy_inspect(Mock()), 2)
            self.assertIn("Invalid context root", say.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
