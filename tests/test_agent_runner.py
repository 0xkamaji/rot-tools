import argparse
from pathlib import Path
from unittest.mock import patch
import unittest

from rotbot.agents import runner
from rotbot.agents.config import CODEX
from rotbot.agents.invocation import AIInvocationResult
from rotbot.contexts import inspection, prompt


class AskAgentTests(unittest.TestCase):
    def inspected_context(self):
        return inspection.InspectedContext(
            None, None, None, None, None, None, None, None,
            Path("/work"),
            inspection.IdentificationSources(
                "not configured", "not configured", "not configured",
                "no matching project context"
            ),
            ()
        )

    def test_ask_uses_shared_freeform_invocation(self):
        args = argparse.Namespace(question=["How", "long?"], agent="codex")

        def invoke(invocation, **_kwargs):
            return AIInvocationResult(invocation, 0, "Answer\n", 2.34, "Codex")

        with patch.object(
            runner, "resolve_provider", return_value=(CODEX, None)
        ), patch.object(
            runner, "inspect_current_context", return_value=self.inspected_context()
        ), patch.object(
            runner,
            "resolve_prompt_context",
            return_value=prompt.PromptContext(None, None, None, None, "/work", "Codex")
        ), patch.object(runner, "invoke", side_effect=invoke) as shared_invoke, patch.object(
            runner, "rot_output_start"
        ), patch.object(runner, "rot_output_line"), patch.object(
            runner, "rot_output_end"
        ), patch.object(runner, "ConversationStore") as store_class, patch.object(
            runner, "rot_say"
        ) as rot_say:
            result = runner.ask_agent(args)

        self.assertEqual(result, 0)
        invocation = shared_invoke.call_args.args[0]
        self.assertEqual(invocation.purpose, "ask")
        self.assertEqual(invocation.parent_command, "ask")
        self.assertFalse(invocation.conversation)
        self.assertIsNone(invocation.structured_output)
        self.assertIn("<user_request>\n\nHow long?\n\n</user_request>", invocation.prompt)
        store = store_class.return_value
        self.assertEqual(store.append_message.call_count, 2)
        self.assertEqual(store.append_message.call_args_list[0].args[1].content, "How long?")
        self.assertEqual(store.append_message.call_args_list[1].args[1].content, "Answer\n")
        self.assertEqual(rot_say.call_args_list[-1].args[0], "Response received in 2.3s.")

    def test_provider_and_context_failures_do_not_invoke(self):
        args = argparse.Namespace(question="Anything?", agent=None)
        with patch.object(
            runner, "resolve_provider", return_value=(None, "unavailable")
        ), patch.object(runner, "invoke") as shared_invoke, patch.object(
            runner, "rot_say"
        ) as rot_say:
            self.assertEqual(runner.ask_agent(args), 127)
        shared_invoke.assert_not_called()
        rot_say.assert_called_once_with("unavailable")

        with patch.object(
            runner, "resolve_provider", return_value=(CODEX, None)
        ), patch.object(
            runner, "inspect_current_context",
            side_effect=inspection.ContextInspectionError("broken context")
        ), patch.object(runner, "invoke") as shared_invoke, patch.object(
            runner, "rot_say"
        ) as rot_say:
            self.assertEqual(runner.ask_agent(args), 2)
        shared_invoke.assert_not_called()
        rot_say.assert_called_once_with("broken context")


if __name__ == "__main__":
    unittest.main()
