from pathlib import Path
import unittest
from unittest.mock import Mock, call, patch

from rotbot.agents.conversation import (
    BackendResult,
    BackendStateReference,
    ConversationError
)
from rotbot.agents import invocation
from rotbot.session import ai
from rotbot.session.history import CommandHistory


class AIConversationTests(unittest.TestCase):
    def backend(self):
        backend = Mock()
        backend.name = "OpenCode"
        backend.prepare.return_value = False
        return backend

    def result(self, response, session_id="ses_abc"):
        return BackendResult(
            response=response,
            remote_state=(BackendStateReference(
                "backend", "opencode", "session", session_id,
                "local_persistent"
            ),),
            model="openai/test-model"
        )

    def test_rot_identity_and_transcript_are_independent_of_backend_session(self):
        backend = self.backend()
        backend.generate.side_effect = (
            self.result("First answer", "ses_one"),
            self.result("Second answer", "ses_two")
        )
        conversation = ai.AIConversation.create(backend)

        with patch.object(
            invocation, "resolve_egress_context", return_value=Mock()
        ), patch.object(
            invocation, "build_ask_prompt", return_value="INITIAL"
        ), patch.object(
            invocation, "build_context_refresh_prompt", return_value="REFRESH"
        ):
            conversation.send("First question", Mock(), Path("/work"))
            conversation.send("Second question", Mock(), Path("/work"))

        self.assertTrue(conversation.id.startswith("rotconv_"))
        self.assertNotIn("ses_one", conversation.id)
        self.assertEqual(
            [(message.role, message.content) for message in conversation.messages],
            [
                ("user", "First question"),
                ("assistant", "First answer"),
                ("user", "Second question"),
                ("assistant", "Second answer")
            ]
        )
        self.assertEqual(
            [reference.state_id for reference in conversation.remote_state],
            ["ses_one", "ses_two"]
        )
        self.assertEqual(conversation.model, "openai/test-model")

    def test_backend_failure_preserves_rot_user_turn_and_prior_transcript(self):
        backend = self.backend()
        backend.generate.side_effect = (
            self.result("First answer"),
            ConversationError("backend unavailable")
        )
        conversation = ai.AIConversation.create(backend)

        with patch.object(invocation, "resolve_egress_context", return_value=Mock()), patch.object(
            invocation, "build_ask_prompt", return_value="INITIAL"
        ):
            conversation.send("First question", Mock(), Path("/work"))
            with self.assertRaisesRegex(ConversationError, "unavailable"):
                conversation.send("Retry this", Mock(), Path("/work"))

        self.assertEqual(
            [(message.role, message.content) for message in conversation.messages],
            [
                ("user", "First question"),
                ("assistant", "First answer"),
                ("user", "Retry this")
            ]
        )
        self.assertEqual(conversation.status, "active")
        self.assertEqual(conversation.remote_state[0].state_id, "ses_abc")

    def test_initial_context_once_and_rot_owned_dirty_refresh(self):
        backend = self.backend()
        backend.generate.side_effect = (
            self.result("one"), self.result("two"), self.result("three")
        )
        conversation = ai.AIConversation.create(backend)
        unchanged = Mock()
        contexts = (unchanged, unchanged, Mock())

        with patch.object(
            invocation, "resolve_egress_context", side_effect=contexts
        ), patch.object(
            invocation, "build_ask_prompt", return_value="INITIAL"
        ) as initial, patch.object(
            invocation, "build_context_refresh_prompt", return_value="REFRESH"
        ) as refresh:
            conversation.send("first", Mock(), Path("/work"))
            conversation.send("second", Mock(), Path("/work"))
            conversation.mark_context_dirty()
            conversation.send("third", Mock(), Path("/other"))

        initial.assert_called_once()
        refresh.assert_called_once()
        self.assertEqual(backend.generate.call_args_list, [
            call("INITIAL", Path("/work"), authority="TALK"),
            call("second", Path("/work"), authority="TALK"),
            call("REFRESH", Path("/other"), authority="TALK")
        ])
        self.assertEqual(conversation.context_version, 2)
        self.assertFalse(conversation.context_dirty)

    def test_command_history_and_semantic_context_are_separate_objects(self):
        backend = self.backend()
        backend.generate.return_value = self.result("answer")
        conversation = ai.AIConversation.create(backend)
        history = CommandHistory(path=Path("/unused"))
        history.add("git status")
        inspected = Mock()

        with patch.object(
            invocation, "resolve_egress_context", return_value=Mock()
        ), patch.object(
            invocation, "build_ask_prompt", return_value="COMPILED ROT CONTEXT"
        ):
            conversation.send("Why?", inspected, Path("/work"))

        self.assertEqual(history.recent(), ["git status"])
        self.assertEqual(
            [(message.role, message.content) for message in conversation.messages],
            [("user", "Why?"), ("assistant", "answer")]
        )
        self.assertNotIn("git status", backend.generate.call_args.args[0])
        self.assertIsNot(inspected, conversation.messages)

    def test_backend_replacement_replays_rot_transcript_for_work_scope(self):
        backend = self.backend()
        backend.prepare.side_effect = (False, True)
        backend.generate.side_effect = (self.result("one"), self.result("two", "ses_new"))
        conversation = ai.AIConversation.create(backend)

        with patch.object(invocation, "resolve_egress_context", return_value=Mock()), patch.object(
            invocation, "build_ask_prompt", return_value="CONTEXT"
        ):
            conversation.send("first", Mock(), Path("/old"), authority="TALK")
            conversation.send("second", Mock(), Path("/new"), authority="WORK")

        second_prompt = backend.generate.call_args_list[1].args[0]
        self.assertIn("<rot_conversation_transcript>", second_prompt)
        self.assertIn("user: first", second_prompt)
        self.assertIn("assistant: one", second_prompt)
        self.assertIn("CONTEXT", second_prompt)


if __name__ == "__main__":
    unittest.main()
