from datetime import datetime
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import Mock, patch

from rotbot.agents.conversation import (
    BackendResult,
    BackendStateReference,
    ConversationError
)
from rotbot.contexts.inspection import IdentificationSources, InspectedContext
from rotbot.session import ai
from rotbot.session.conversations import (
    ConversationStore,
    ConversationStoreError,
    conversations_path
)


def inspected(cwd, project="rotbot"):
    return InspectedContext(
        "Rot", "assistant-id", "Kamaji", "user-id", "laptop", "machine-id",
        project, f"{project}-id" if project else None, Path(cwd),
        IdentificationSources("local", "local", "local", "source"), ()
    )


class ConversationStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.store = ConversationStore(self.root / "data" / "rotbot" / "conversations")
        self.backend = Mock()
        self.backend.name = "OpenCode"
        self.backend.prepare.return_value = False
        self.backend.generate.side_effect = (
            BackendResult(
                "First answer",
                (BackendStateReference(
                    "backend", "opencode", "session", "ses_backend",
                    "local_persistent"
                ),),
                "openai/test-model"
            ),
            BackendResult("Second answer", (), "openai/test-model")
        )

    def tearDown(self):
        self.temporary_directory.cleanup()

    def send(self, conversation, message, authority="TALK"):
        with patch.object(ai, "resolve_prompt_context", return_value=Mock()), patch.object(
            ai, "build_ask_prompt", return_value="INITIAL"
        ), patch.object(ai, "build_context_refresh_prompt", return_value="REFRESH"):
            return conversation.send(
                message, inspected(self.root), self.root, authority=authority
            )

    def test_xdg_data_path_is_private_local_and_not_portable_context(self):
        environment = {"XDG_DATA_HOME": str(self.root / "xdg")}
        path = conversations_path(environment)

        self.assertEqual(path, self.root / "xdg" / "rotbot" / "conversations")
        self.assertNotIn("context", path.parts)
        with self.assertRaisesRegex(ConversationStoreError, "absolute"):
            conversations_path({"XDG_DATA_HOME": "relative"})

    def test_first_turn_creates_private_store_and_backend_id_is_only_metadata(self):
        conversation = ai.AIConversation.create(self.backend, self.store)
        directory = self.store.root / conversation.id
        self.assertFalse(directory.exists())

        self.send(conversation, "First question")
        loaded = self.store.load(conversation.id)

        self.assertTrue(directory.is_dir())
        self.assertNotEqual(conversation.id, "ses_backend")
        self.assertEqual(loaded.remote_state[0]["id"], "ses_backend")
        self.assertEqual(
            [(message.role, message.content, message.status) for message in loaded.messages],
            [("user", "First question", "complete"),
             ("assistant", "First answer", "complete")]
        )
        self.assertTrue(all(message.id.startswith("msg_") for message in loaded.messages))
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE(self.store.root.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)
            self.assertEqual(
                stat.S_IMODE((directory / "metadata.toml").stat().st_mode), 0o600
            )
            self.assertEqual(
                stat.S_IMODE((directory / "transcript.jsonl").stat().st_mode), 0o600
            )

    def test_multiple_modes_preserve_order_and_close_survives_reload(self):
        conversation = ai.AIConversation.create(self.backend, self.store)
        self.send(conversation, "First question", "TALK")
        self.send(conversation, "Second question", "WORK")
        conversation.close()

        loaded = ConversationStore(self.store.root).load(conversation.id)
        self.assertEqual(loaded.status, "closed")
        self.assertIsNotNone(loaded.closed_at)
        self.assertEqual(
            [(message.role, message.content, message.authority) for message in loaded.messages],
            [
                ("user", "First question", "TALK"),
                ("assistant", "First answer", "TALK"),
                ("user", "Second question", "WORK"),
                ("assistant", "Second answer", "WORK")
            ]
        )
        self.assertTrue((self.store.root / conversation.id).exists())

    def test_failed_inference_retains_user_turn_as_failed(self):
        self.backend.generate.side_effect = ConversationError("offline")
        conversation = ai.AIConversation.create(self.backend, self.store)

        with self.assertRaisesRegex(ConversationError, "offline"):
            self.send(conversation, "Keep this question")

        loaded = self.store.load(conversation.id)
        self.assertEqual(len(loaded.messages), 1)
        self.assertEqual(loaded.messages[0].content, "Keep this question")
        self.assertEqual(loaded.messages[0].status, "failed")

    def test_failed_inference_records_backend_state_if_backend_observed_it(self):
        self.backend.generate.side_effect = ConversationError("offline")
        self.backend.known_remote_state.return_value = (
            BackendStateReference(
                "backend", "opencode", "session", "ses_failed",
                "local_persistent"
            ),
        )
        conversation = ai.AIConversation.create(self.backend, self.store)

        with self.assertRaisesRegex(ConversationError, "offline"):
            self.send(conversation, "Keep provider provenance")

        loaded = self.store.load(conversation.id)
        self.assertEqual(loaded.remote_state[0]["id"], "ses_failed")

    def test_transcript_is_jsonl_and_metadata_is_inspectable_toml(self):
        conversation = ai.AIConversation.create(self.backend, self.store)
        self.send(conversation, "First question")
        directory = self.store.root / conversation.id

        records = [
            json.loads(line)
            for line in (directory / "transcript.jsonl").read_text(
                encoding="utf-8"
            ).splitlines()
        ]
        metadata = (directory / "metadata.toml").read_text(encoding="utf-8")

        self.assertEqual(records[0]["type"], "message")
        self.assertEqual(records[0]["content"], "First question")
        self.assertEqual(records[1]["type"], "message_status")
        self.assertIn('status = "active"', metadata)
        self.assertIn('id = "ses_backend"', metadata)

    def test_list_handles_missing_store_and_rejects_unsafe_id(self):
        self.assertEqual(self.store.list(), [])
        with self.assertRaisesRegex(ConversationStoreError, "Invalid Rot conversation ID"):
            self.store.load("../../context")

    def test_close_cleans_backend_and_can_retry_after_metadata_failure(self):
        conversation = ai.AIConversation.create(self.backend, self.store)
        self.send(conversation, "First question")
        with patch.object(
            self.store, "close", side_effect=ConversationStoreError("denied")
        ), self.assertRaisesRegex(ConversationStoreError, "denied"):
            conversation.close()

        self.backend.close.assert_called_once_with()
        self.assertIsNone(conversation.closed_at)

        conversation.close()
        self.assertEqual(self.store.load(conversation.id).status, "closed")

    def test_malformed_transcript_is_reported_as_store_error(self):
        conversation = ai.AIConversation.create(self.backend, self.store)
        self.send(conversation, "First question")
        transcript = self.store.root / conversation.id / "transcript.jsonl"
        transcript.write_text('"not an object"\n', encoding="utf-8")
        if os.name != "nt":
            transcript.chmod(0o600)

        with self.assertRaisesRegex(ConversationStoreError, "Invalid conversation"):
            self.store.load(conversation.id)


if __name__ == "__main__":
    unittest.main()
