from contextlib import redirect_stdout
from datetime import datetime
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from rotbot.commands import ai as commands
from rotbot.contexts.inspection import IdentificationSources, InspectedContext
from rotbot.session.conversations import ConversationStore


class AISessionCommandTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.store = ConversationStore(self.root / "conversations")
        self.id = "rotconv_" + "a" * 32
        context = InspectedContext(
            "Rot", "assistant-id", "Kamaji", "user-id", "laptop", "machine-id",
            "rotbot", "project-id", self.root,
            IdentificationSources("local", "local", "local", "source"), ()
        )
        self.store.create(
            self.id, datetime(2026, 8, 12, 14, 15).astimezone(), context,
            self.root, "OpenCode"
        )

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_sessions_marks_current_rot_conversation(self):
        output = io.StringIO()
        args = Mock(active_conversation_id=self.id)
        with patch.object(commands, "ConversationStore", return_value=self.store), redirect_stdout(output):
            result = commands.ai_sessions(args)

        self.assertEqual(result, 0)
        self.assertIn("rotconv_aaaaaaaaaaaaaaaaa", output.getvalue())
        self.assertIn("current", output.getvalue())

    def test_show_loads_closed_conversation_without_backend_access(self):
        self.store.close(self.id, datetime(2026, 8, 12, 15, 0).astimezone())
        with patch.object(commands, "ConversationStore", return_value=self.store), patch.object(
            commands, "rot_say"
        ) as rot_say:
            result = commands.ai_session_show(Mock(id=self.id))

        self.assertEqual(result, 0)
        rendered = rot_say.call_args.args[0]
        self.assertIn(f"ID:          {self.id}", rendered)
        self.assertIn("Status:      closed", rendered)
        self.assertIn("TRANSCRIPT", rendered)

    def test_show_without_id_prompts_for_numbered_conversation(self):
        with patch.object(
            commands, "ConversationStore", return_value=self.store
        ), patch.object(
            commands, "rot_say"
        ) as rot_say, patch.object(
            commands, "input", return_value="1"
        ) as read:
            result = commands.ai_session_show(Mock(id=None))

        self.assertEqual(result, 0)
        self.assertIn("CHOOSE A ROT AI CONVERSATION", rot_say.call_args_list[0].args[0])
        self.assertIn("project-id", rot_say.call_args_list[0].args[0])
        self.assertIn(f"ID:          {self.id}", rot_say.call_args_list[-1].args[0])
        read.assert_called_once_with("> ")

    def test_show_menu_retries_invalid_choice_and_allows_exit(self):
        with patch.object(
            commands, "ConversationStore", return_value=self.store
        ), patch.object(
            commands, "rot_say"
        ) as rot_say, patch.object(
            commands, "input", side_effect=("9", "exit")
        ):
            result = commands.ai_session_show(Mock(id=None))

        self.assertEqual(result, 0)
        self.assertIn("Choose a number from 1 to 1", rot_say.call_args_list[-1].args[0])


if __name__ == "__main__":
    unittest.main()
