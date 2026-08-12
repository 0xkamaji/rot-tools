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
        ) as popen:
            first_result = backend.generate(
                "initial context", Path("/one"), authority="TALK"
            )
            second_result = backend.generate(
                "follow-up", Path("/two"), authority="TALK"
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
        permissions = json.loads(
            popen.call_args_list[0].kwargs["env"]["OPENCODE_PERMISSION"]
        )
        self.assertIs(
            popen.call_args_list[0].kwargs["stderr"],
            conversation.subprocess.STDOUT
        )
        self.assertEqual(permissions, {"*": "deny"})
        inline_config = json.loads(
            popen.call_args_list[0].kwargs["env"]["OPENCODE_CONFIG_CONTENT"]
        )
        self.assertEqual(
            inline_config["agent"][conversation.TALK_AGENT]["permission"],
            {"*": "deny"}
        )
        self.assertIn("--pure", first_command)
        self.assertEqual(
            first_command[first_command.index("--agent") + 1],
            conversation.TALK_AGENT
        )

    def test_work_allows_workspace_tools_but_denies_external_directory(self):
        process = self.process([
            event("text", part={"type": "text", "text": "Worked"})
        ])
        backend = conversation.OpenCodeBackend()
        with patch.object(conversation, "which", return_value="/bin/opencode"), patch.object(
            conversation.subprocess, "Popen", return_value=process
        ) as popen:
            backend.generate("work", Path("/scope"), authority="WORK")

        permissions = json.loads(popen.call_args.kwargs["env"]["OPENCODE_PERMISSION"])
        self.assertEqual(permissions["*"], "allow")
        self.assertEqual(permissions["external_directory"], "deny")
        self.assertEqual(permissions["question"], "deny")

    def test_talk_deny_all_cannot_be_overridden_by_auto_defaults(self):
        process = self.process([
            event("text", part={"type": "text", "text": "Talked"})
        ])
        backend = conversation.OpenCodeBackend()
        with patch.object(conversation, "which", return_value="/bin/opencode"), patch.object(
            conversation.subprocess, "Popen", return_value=process
        ) as popen:
            backend.generate("talk", Path("/scope"), authority="TALK")

        permissions = json.loads(popen.call_args.kwargs["env"]["OPENCODE_PERMISSION"])
        self.assertEqual(permissions, {"*": "deny"})
        self.assertNotIn("--auto", popen.call_args.args[0])

    def test_talk_preserves_inline_config_and_overrides_its_owned_agent(self):
        process = self.process([
            event("text", part={"type": "text", "text": "Talked"})
        ])
        existing = {
            "model": "provider/model",
            "agent": {conversation.TALK_AGENT: {"permission": {"*": "allow"}}}
        }
        backend = conversation.OpenCodeBackend()
        with patch.dict(
            conversation.os.environ,
            {"OPENCODE_CONFIG_CONTENT": json.dumps(existing)}
        ), patch.object(
            conversation, "which", return_value="/bin/opencode"
        ), patch.object(
            conversation.subprocess, "Popen", return_value=process
        ) as popen:
            backend.generate("talk", Path("/scope"), authority="TALK")

        inline_config = json.loads(
            popen.call_args.kwargs["env"]["OPENCODE_CONFIG_CONTENT"]
        )
        self.assertEqual(inline_config["model"], "provider/model")
        self.assertEqual(
            inline_config["agent"][conversation.TALK_AGENT]["permission"],
            {"*": "deny"}
        )

    def test_invalid_inline_config_is_a_conversation_error(self):
        backend = conversation.OpenCodeBackend()
        with patch.dict(
            conversation.os.environ,
            {"OPENCODE_CONFIG_CONTENT": "not-json"}
        ), patch.object(
            conversation, "which", return_value="/bin/opencode"
        ), self.assertRaisesRegex(
            conversation.ConversationError, "Invalid OPENCODE_CONFIG_CONTENT"
        ):
            backend.generate("talk", Path("/scope"), authority="TALK")

    def test_work_in_new_directory_replaces_backend_session_scope(self):
        backend = conversation.OpenCodeBackend()
        backend.directory = Path("/old")
        backend.session_id = "ses_old"

        replaced = backend.prepare("WORK", Path("/new"))

        self.assertTrue(replaced)
        self.assertEqual(backend.directory, Path("/new"))
        self.assertIsNone(backend.session_id)

    def test_known_remote_state_exposes_observed_backend_session(self):
        backend = conversation.OpenCodeBackend()
        self.assertEqual(backend.known_remote_state(), ())

        backend.session_id = "ses_observed"
        references = backend.known_remote_state()

        self.assertEqual(len(references), 1)
        self.assertEqual(references[0].state_id, "ses_observed")
        self.assertEqual(references[0].provider, "opencode")

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
