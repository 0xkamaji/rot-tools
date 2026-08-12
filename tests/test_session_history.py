import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import Mock, patch

from rotbot.contexts import config
from rotbot.contexts import prompt
from rotbot.session import history
from rotbot.ui import input as input_ui


class CommandHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.path = self.root / "config" / "rot" / "history"

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_default_path_uses_private_rot_config_not_portable_context(self):
        with patch.dict(
            os.environ, {"XDG_CONFIG_HOME": str(self.root)}, clear=True
        ):
            path = history.history_path()

        self.assertEqual(path, config.config_path({"XDG_CONFIG_HOME": str(self.root)}).parent / "history")
        self.assertNotIn("context", path.parts)

    def test_add_ignores_empty_invalid_and_consecutive_duplicates(self):
        commands = history.CommandHistory(self.path)

        self.assertFalse(commands.add(""))
        self.assertFalse(commands.add("   "))
        self.assertTrue(commands.add("git status"))
        self.assertFalse(commands.add("git status"))
        self.assertFalse(commands.add("git\nstatus"))
        self.assertTrue(commands.add("context inspect"))

        self.assertEqual(commands.recent(), ["git status", "context inspect"])

    def test_maximum_and_recent_limits_preserve_order(self):
        commands = history.CommandHistory(self.path, max_entries=3)
        for command in ("one", "two", "three", "four"):
            commands.add(command)

        self.assertEqual(commands.recent(), ["two", "three", "four"])
        self.assertEqual(commands.recent(2), ["three", "four"])

    def test_save_and_load_persist_between_instances_with_private_mode(self):
        first = history.CommandHistory(self.path)
        first.add("git status")
        first.add("context inspect")
        first.save()

        second = history.CommandHistory(self.path)
        second.load()

        self.assertEqual(second.recent(), ["git status", "context inspect"])
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)

    def test_missing_history_loads_as_empty(self):
        commands = history.CommandHistory(self.path)

        commands.load()

        self.assertEqual(commands.recent(), [])

    def test_unsafe_or_unreadable_history_raises_convenience_error(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text("private command\n", encoding="utf-8")
        if os.name != "nt":
            self.path.chmod(0o644)
            with self.assertRaisesRegex(history.HistoryError, "not private"):
                history.CommandHistory(self.path).load()

        with patch.object(Path, "read_text", side_effect=OSError("denied")):
            self.path.chmod(0o600)
            with self.assertRaisesRegex(history.HistoryError, "Could not read"):
                history.CommandHistory(self.path).load()

    def test_unwritable_history_reports_error_and_cleans_temporary_file(self):
        commands = history.CommandHistory(self.path)
        commands.add("git status")

        with patch.object(history.os, "replace", side_effect=OSError("denied")), self.assertRaisesRegex(
            history.HistoryError, "Could not write"
        ):
            commands.save()

        self.assertEqual(tuple(self.path.parent.glob("history.*.tmp")), ())

    def test_history_is_not_part_of_ai_prompt_context(self):
        commands = history.CommandHistory(self.path)
        commands.add("PRIVATE_HISTORY_MUST_NOT_ENTER_AI_PROMPT")
        context = prompt.PromptContext(
            assistant=None,
            user=None,
            machine=None,
            project=None,
            working_directory="/work",
            execution_backend="Codex"
        )

        rendered = prompt.build_ask_prompt(context, "Question")

        self.assertNotIn("PRIVATE_HISTORY_MUST_NOT_ENTER_AI_PROMPT", rendered)


class InputBackendTests(unittest.TestCase):
    def test_readline_backend_loads_and_records_service_entries(self):
        readline = Mock()
        backend = input_ui.ReadlineInput(readline)

        backend.prepare(["git status", "context inspect"])
        backend.record("machine inspect")

        readline.clear_history.assert_called_once_with()
        self.assertEqual(readline.add_history.call_args_list, [
            unittest.mock.call("git status"),
            unittest.mock.call("context inspect"),
            unittest.mock.call("machine inspect")
        ])
        readline.set_auto_history.assert_called_once_with(False)

    def test_basic_backend_uses_normal_input(self):
        backend = input_ui.BasicInput()
        with patch.object(input_ui.builtins, "input", return_value="pwd") as read:
            self.assertEqual(backend.read("rot> "), "pwd")
        read.assert_called_once_with("rot> ")


if __name__ == "__main__":
    unittest.main()
