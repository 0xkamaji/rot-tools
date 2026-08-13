from datetime import datetime
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from rotbot.session import last


class LastResponseHelperTests(unittest.TestCase):
    def test_visual_precedes_editor_and_edit_preserves_multiline_text(self):
        observed = {}

        def run(command, check):
            observed["command"] = command
            path = Path(command[-1])
            observed["initial"] = path.read_text(encoding="utf-8")
            observed["path"] = path
            path.write_text("edited\nmultiline\n", encoding="utf-8")
            return type("Completed", (), {"returncode": 0})()

        with patch.object(last.subprocess, "run", side_effect=run):
            edited = last.edit_text(
                "original\ntext", {"VISUAL": "visual-editor --wait", "EDITOR": "editor"}
            )

        self.assertEqual(observed["command"][:2], ["visual-editor", "--wait"])
        self.assertEqual(observed["initial"], "original\ntext")
        self.assertEqual(edited, "edited\nmultiline\n")
        self.assertFalse(observed["path"].exists())

    def test_editor_failure_cleans_temporary_file(self):
        observed = {}

        def run(command, check):
            observed["path"] = Path(command[-1])
            return type("Completed", (), {"returncode": 7})()

        with patch.object(last.subprocess, "run", side_effect=run), self.assertRaises(
            last.LastResponseError
        ):
            last.edit_text("unchanged", {"EDITOR": "editor"})

        self.assertFalse(observed["path"].exists())

    def test_save_uses_xdg_private_permissions_exact_text_and_collisions(self):
        with tempfile.TemporaryDirectory() as temporary, patch.dict(
            os.environ, {"XDG_DATA_HOME": temporary}, clear=True
        ):
            now = datetime(2026, 8, 13, 13, 52)
            first = last.save_text("exact\ntext", now, category="debug")
            second = last.save_text("exact\ntext", now, category="debug")

            self.assertEqual(first.parent, Path(temporary) / "rotbot" / "debug")
            self.assertNotEqual(first, second)
            self.assertEqual(first.read_text(encoding="utf-8"), "exact\ntext")
            if os.name != "nt":
                self.assertEqual(stat.S_IMODE(first.parent.stat().st_mode), 0o700)
                self.assertEqual(stat.S_IMODE(first.stat().st_mode), 0o600)

    def test_save_fallback_uses_home_local_share(self):
        with tempfile.TemporaryDirectory() as temporary, patch.dict(
            os.environ, {"HOME": temporary}, clear=True
        ):
            path = last.save_text(
                "text", datetime(2026, 8, 13, 13, 52), category="responses"
            )

        self.assertEqual(
            path,
            Path(temporary) / ".local" / "share" / "rotbot" / "responses"
            / "20260813_135200_ai-response.txt"
        )

    def test_save_rejects_arbitrary_categories_and_sanitizes_hint(self):
        with self.assertRaises(last.LastResponseError):
            last.save_text("text", category="../project")
        with tempfile.TemporaryDirectory() as temporary, patch.dict(
            os.environ, {"XDG_DATA_HOME": temporary}, clear=True
        ):
            path = last.save_text(
                "text", datetime(2026, 8, 13, 13, 52),
                category="debug", filename_hint="Debug Last Ask / unsafe"
            )
        self.assertEqual(path.name, "20260813_135200_debug-last-ask-unsafe.txt")


if __name__ == "__main__":
    unittest.main()
