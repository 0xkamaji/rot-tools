import argparse
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from rotbot.contexts import loader
from rotbot.integrations.signalrot import commands as signalrot
from rotbot.integrations.signalrot import context as signalrot_context


class SignalRotContextCompatibilityTests(unittest.TestCase):
    def test_refresh_still_delegates_to_specialized_implementation(self):
        args = argparse.Namespace(refresh=True, agent="codex", note="check")
        repository = Path("/signalrot")
        web_root = Path.cwd()
        status = SimpleNamespace(returncode=0, stdout="clean\n", stderr="")
        deployment = SimpleNamespace(
            returncode=0,
            stdout="deployment diff\n",
            stderr=""
        )

        with patch.object(signalrot, "_repo_path", return_value=repository), patch.object(
            signalrot,
            "_web_root",
            return_value=web_root
        ), patch.object(signalrot, "_validate_repo", return_value=True), patch.object(
            signalrot,
            "_capture",
            side_effect=(status, deployment)
        ), patch.object(
            signalrot,
            "refresh_signalrot_context",
            return_value=7
        ) as refresh:
            result = signalrot.sr_context(args)

        self.assertEqual(result, 7)
        refresh.assert_called_once_with(
            args,
            repository,
            web_root,
            "clean",
            "deployment diff"
        )

    def test_existing_prompt_block_still_reads_both_signalrot_files(self):
        block = signalrot_context.signalrot_context_block()
        identity_path, state_path = loader.context_paths("signalrot")

        self.assertIn(
            identity_path.read_text(encoding="utf-8"),
            block
        )
        self.assertIn(
            state_path.read_text(encoding="utf-8"),
            block
        )

    def test_existing_prompt_block_does_not_include_sibling_vision(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            context_directory = root / "signalrot"
            context_directory.mkdir()
            identity_path = context_directory / "identity.md"
            state_path = context_directory / "state.md"
            vision_path = context_directory / "vision.md"
            match_path = context_directory / "match.md"
            identity_path.write_text("identity only", encoding="utf-8")
            state_path.write_text("state only", encoding="utf-8")
            vision_path.write_text("vision must stay separate", encoding="utf-8")
            match_path.write_text("match must stay separate", encoding="utf-8")

            with patch.object(loader, "CONTEXT_ROOT", root):
                block = signalrot_context.signalrot_context_block()

        self.assertIn("identity only", block)
        self.assertIn("state only", block)
        self.assertNotIn("vision must stay separate", block)
        self.assertNotIn("match must stay separate", block)

    def test_signalrot_ai_review_uses_generic_context_prompt(self):
        identity_path, state_path = loader.context_paths("signalrot")
        match_path = identity_path.parent / "match.md"

        with patch.object(
            signalrot,
            "stream_agent",
            return_value=(0, "review", 0.1)
        ) as stream_agent, patch.object(signalrot, "rot_say"):
            result = signalrot._review_task("Review task", Path.cwd(), "Reviewing...")

        self.assertEqual(result, 0)
        prompt = stream_agent.call_args.args[0]
        self.assertIn(identity_path.read_text(encoding="utf-8"), prompt)
        self.assertIn(state_path.read_text(encoding="utf-8"), prompt)
        self.assertNotIn(match_path.read_text(encoding="utf-8"), prompt)

    def test_refresh_modifies_only_state(self):
        refreshed_at = "2026-08-10 17:16 UTC"
        sections = ("OPPSEC", "Hacks", "Signals", "Beats", "Frames", "Contact")
        output = (
            "# SignalRot Current State\n\n"
            f"Last refreshed: {refreshed_at}\n\n"
            "## Section updates\n\n"
            + "\n\n".join(
                f"### {section}\n- Last changed: unknown"
                for section in sections
            )
        )

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            context_directory = root / "signalrot"
            context_directory.mkdir()
            identity_path = context_directory / "identity.md"
            state_path = context_directory / "state.md"
            vision_path = context_directory / "vision.md"
            match_path = context_directory / "match.md"
            identity_path.write_text("identity unchanged", encoding="utf-8")
            state_path.write_text("old state", encoding="utf-8")
            vision_path.write_text("vision unchanged", encoding="utf-8")
            match_path.write_text("match unchanged", encoding="utf-8")

            with patch.object(loader, "CONTEXT_ROOT", root), patch.object(
                signalrot_context,
                "datetime"
            ) as datetime_mock, patch.object(
                signalrot_context,
                "stream_agent",
                return_value=(0, output, 0.5)
            ), patch.object(
                signalrot_context,
                "show_signalrot_context",
                return_value=0
            ), patch.object(signalrot_context, "rot_say"):
                datetime_mock.now.return_value.strftime.return_value = refreshed_at
                result = signalrot_context.refresh_signalrot_context(
                    argparse.Namespace(agent=None, note=None),
                    root,
                    root,
                    "",
                    ""
                )

            self.assertEqual(result, 0)
            self.assertEqual(
                identity_path.read_text(encoding="utf-8"),
                "identity unchanged"
            )
            self.assertEqual(
                vision_path.read_text(encoding="utf-8"),
                "vision unchanged"
            )
            self.assertEqual(
                match_path.read_text(encoding="utf-8"),
                "match unchanged"
            )
            self.assertEqual(
                state_path.read_text(encoding="utf-8"),
                output + "\n"
            )
            self.assertFalse((context_directory / "state.tmp").exists())


if __name__ == "__main__":
    unittest.main()
