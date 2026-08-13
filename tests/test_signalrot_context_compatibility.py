import argparse
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from rotbot.integrations.signalrot import commands as signalrot


class SignalRotContextCompatibilityTests(unittest.TestCase):
    def test_full_context_delegates_to_generic_context_display(self):
        args = argparse.Namespace(full=True)

        with patch.object(signalrot, "context_show", return_value=6) as context_show:
            result = signalrot.sr_context(args)

        self.assertEqual(result, 6)
        shown_args = context_show.call_args.args[0]
        self.assertEqual(shown_args.name, "signalrot")
        self.assertFalse(shown_args.vision)

    def test_summary_context_uses_specialized_display(self):
        with patch.object(signalrot, "show_signalrot_context", return_value=4) as show:
            result = signalrot.sr_context(argparse.Namespace(full=False))

        self.assertEqual(result, 4)
        show.assert_called_once_with()

    def test_diff_prints_rsync_dry_run_output(self):
        repository = Path("/signalrot/source")
        production = Path("/signalrot/production")
        dry_run = SimpleNamespace(
            returncode=0,
            stdout=">f.st...... index.html\n*deleting old.html\n",
            stderr=""
        )

        with patch.object(signalrot, "_repo_path", return_value=repository), patch.object(
            signalrot, "_web_root", return_value=production
        ), patch.object(signalrot, "_validate_repo", return_value=True), patch.object(
            Path, "is_dir", return_value=True
        ), patch.object(signalrot, "_capture", return_value=dry_run) as capture, patch.object(
            signalrot, "rot_say"
        ) as rot_say:
            result = signalrot.sr_diff(argparse.Namespace())

        self.assertEqual(result, 0)
        command = capture.call_args.args[0]
        self.assertIn("--dry-run", command)
        self.assertIn("--itemize-changes", command)
        output = "\n".join(call.args[0] for call in rot_say.call_args_list)
        self.assertIn(">f.st...... index.html\n*deleting old.html", output)


if __name__ == "__main__":
    unittest.main()
