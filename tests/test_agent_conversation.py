import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import call, Mock, patch

from rotbot.agents import conversation


def event(event_type, session_id="ses_rot", **data):
    return json.dumps({"type": event_type, "sessionID": session_id, **data}) + "\n"


class OpenCodeBackendTests(unittest.TestCase):
    def process(self, lines, returncode=0, errors=()):
        return SimpleNamespace(
            stdout=iter((*lines, *errors)),
            wait=Mock(return_value=returncode),
            terminate=Mock()
        )

    def test_first_turn_extracts_session_and_second_turn_reuses_it(self):
        first = self.process([
            event("step_start"),
            event("text", part={"type": "text", "text": "First answer"})
        ])
        second = self.process([
            event("text", part={"type": "text", "text": "Second answer"})
        ])
        backend = conversation.OpenCodeBackend()

        with patch.object(conversation, "which", return_value="/bin/opencode"), patch.object(
            conversation.subprocess, "Popen", side_effect=(first, second)
        ) as popen, patch.object(conversation, "rot_output_start"), patch.object(
            conversation, "rot_output_line"
        ) as output_line, patch.object(conversation, "rot_output_end"):
            first_result = backend.generate(
                "initial context", Path("/one"), display_question="first"
            )
            second_result = backend.generate(
                "follow-up", Path("/two"), display_question="second"
            )

        self.assertEqual(backend.session_id, "ses_rot")
        self.assertEqual(first_result.response, "First answer")
        self.assertEqual(second_result.response, "Second answer")
        self.assertEqual(first_result.remote_state[0].state_id, "ses_rot")
        first_command = popen.call_args_list[0].args[0]
        second_command = popen.call_args_list[1].args[0]
        self.assertNotIn("--session", first_command)
        self.assertEqual(
            second_command[second_command.index("--session") + 1], "ses_rot"
        )
        self.assertEqual(popen.call_args_list[1].kwargs["cwd"], Path("/one"))
        self.assertEqual(backend.directory, Path("/one"))
        self.assertEqual(output_line.call_args_list, [call("First answer"), call("Second answer")])
        permissions = json.loads(
            popen.call_args_list[0].kwargs["env"]["OPENCODE_PERMISSION"]
        )
        self.assertIs(
            popen.call_args_list[0].kwargs["stderr"],
            conversation.subprocess.STDOUT
        )
        self.assertEqual(permissions["bash"], "deny")
        self.assertEqual(permissions["edit"], "deny")

    def test_failure_and_missing_backend_are_conversation_errors(self):
        backend = conversation.OpenCodeBackend()
        with patch.object(conversation, "which", return_value=None), self.assertRaisesRegex(
            conversation.ConversationError, "not installed"
        ):
            backend.generate("hello", Path("/tmp"))

        failed = self.process([], returncode=1, errors=("provider failed\n",))
        with patch.object(conversation, "which", return_value="/bin/opencode"), patch.object(
            conversation.subprocess, "Popen", return_value=failed
        ), self.assertRaisesRegex(conversation.ConversationError, "provider failed"):
            backend.generate("hello", Path("/tmp"))

    def test_interrupt_terminates_request_without_closing_session(self):
        class InterruptingOutput:
            def __iter__(self):
                raise KeyboardInterrupt

        process = self.process([])
        process.stdout = InterruptingOutput()
        backend = conversation.OpenCodeBackend()
        backend.session_id = "ses_rot"

        with patch.object(conversation, "which", return_value="/bin/opencode"), patch.object(
            conversation.subprocess, "Popen", return_value=process
        ), self.assertRaises(KeyboardInterrupt):
            backend.generate("hello", Path("/tmp"))

        process.terminate.assert_called_once_with()
        self.assertEqual(backend.session_id, "ses_rot")


if __name__ == "__main__":
    unittest.main()
