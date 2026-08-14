from types import SimpleNamespace
from unittest.mock import Mock, patch
import unittest

from rotbot.agents import invocation
from rotbot.agents.config import OPENCODE
from rotbot.contexts.prompt import PromptContext
from rotbot.ui.ai import AIActivityPresenter


class AIInvocationTests(unittest.TestCase):
    def request(self, purpose="ask", **kwargs):
        context = PromptContext(None, None, None, None, "/work", "OpenCode")
        return invocation.AIRequest(
            purpose, "ask", "prompt", agent_name="opencode",
            persistent_context=context if purpose == "ask" else None,
            **kwargs
        )

    def process(self, stdout=(), stderr=(), returncode=0):
        return SimpleNamespace(
            stdout=iter(stdout), stderr=iter(stderr),
            wait=Mock(return_value=returncode), kill=Mock()
        )

    def test_request_and_plan_exclude_ui_and_persistence_flags(self):
        removed = {"persist_conversation", "display_output", "stream_output"}
        self.assertTrue(removed.isdisjoint(invocation.AIRequest.__dataclass_fields__))
        self.assertTrue(
            removed.isdisjoint(invocation.AIInvocationPlan.__dataclass_fields__)
        )

    def test_freeform_returns_stdout_and_emits_lifecycle(self):
        events = []
        lines = []
        process = self.process(("Hello\n",), ("> model banner\n",))
        request = self.request()
        with patch.object(
            invocation, "resolve_provider", return_value=(OPENCODE, None)
        ), patch.object(invocation, "start_provider_process", return_value=process):
            result = invocation.invoke(request, on_event=events.append, on_output=lines.append)

        self.assertTrue(result.successful)
        self.assertEqual(result.output, "Hello\n")
        self.assertEqual(lines, ["Hello\n"])
        self.assertEqual(events, ["preparing", "started", "streaming", "completed"])

    def test_structured_validation_retries_once(self):
        first = self.process(("bad\n",))
        second = self.process(("42\n",))
        request = self.request(
            "context_development", output_contract="integer", retries=1
        )
        events = []

        def validate(output):
            if not output.strip().isdigit():
                raise ValueError("not an integer")
            return int(output)

        with patch.object(
            invocation, "resolve_provider", return_value=(OPENCODE, None)
        ), patch.object(
            invocation, "start_provider_process", side_effect=(first, second)
        ) as start:
            result = invocation.invoke(request, validator=validate, on_event=events.append)

        self.assertTrue(result.successful)
        self.assertEqual(result.value, 42)
        self.assertEqual(result.attempts, 2)
        self.assertEqual(start.call_count, 2)
        self.assertIn("retrying", events)
        command = start.call_args_list[0].args[0]
        self.assertIn("OUTPUT CONTRACT\ninteger", command[-1])

    def test_retry_exhaustion_and_provider_failure_are_structured(self):
        request = self.request("context_development", retries=1)
        with patch.object(
            invocation, "resolve_provider", return_value=(OPENCODE, None)
        ), patch.object(
            invocation, "start_provider_process",
            side_effect=(self.process(("bad",)), self.process(("bad",)))
        ):
            result = invocation.invoke(
                request, validator=lambda _output: (_ for _ in ()).throw(ValueError("bad JSON"))
            )
        self.assertFalse(result.successful)
        self.assertEqual(result.validation_error, "bad JSON")
        self.assertEqual(result.attempts, 2)

        with patch.object(
            invocation, "resolve_provider", return_value=(None, "provider unavailable")
        ):
            result = invocation.invoke(request)
        self.assertEqual(result.returncode, 127)
        self.assertEqual(result.validation_error, "provider unavailable")

    def test_prepare_exposes_available_and_selected_material_without_execution(self):
        context = PromptContext(None, None, None, None, "/work", "OpenCode")
        messages = (
            invocation.ConversationMessage("user", "Earlier question"),
            invocation.ConversationMessage("assistant", "Earlier answer")
        )
        request = invocation.AIRequest(
            "conversation", "interactive", "Next question",
            agent_name="opencode", persistent_context=context,
            conversation_messages=messages
        )

        with patch.object(
            invocation, "resolve_provider", return_value=(OPENCODE, None)
        ), patch.object(invocation, "start_provider_process") as start:
            plan = invocation.prepare(request)

        start.assert_not_called()
        self.assertIs(plan.available_persistent_context, context)
        self.assertIs(plan.selected_persistent_context, context)
        self.assertEqual(plan.available_conversation, messages)
        self.assertEqual(plan.selected_conversation, messages)
        self.assertTrue(plan.context_sent)
        self.assertTrue(plan.conversation_sent)
        self.assertIn("Earlier answer", plan.provider_input)

    def test_active_provider_state_does_not_resend_transcript_or_context(self):
        context = PromptContext(None, None, None, None, "/work", "OpenCode")
        fingerprint = invocation.hashlib.sha256(
            repr(("egress", context)).encode("utf-8")
        ).hexdigest()
        request = invocation.AIRequest(
            "conversation", "interactive", "Next question",
            agent_name="opencode", persistent_context=context,
            conversation_messages=(
                invocation.ConversationMessage("assistant", "Earlier answer"),
            ),
            provider_state=(object(),),
            previous_context_fingerprint=fingerprint
        )

        with patch.object(
            invocation, "resolve_provider", return_value=(OPENCODE, None)
        ), patch.object(
            invocation, "get_agent_trust", return_value="external"
        ):
            plan = invocation.prepare(request)

        self.assertEqual(plan.provider_input, "Next question")
        self.assertFalse(plan.context_sent)
        self.assertFalse(plan.conversation_sent)
        self.assertEqual(plan.selected_conversation, ())

    def test_prepare_assembles_task_evidence_and_contract_into_exact_input(self):
        request = invocation.AIRequest(
            "context_development", "context develop", "Draft project context.",
            agent_name="opencode",
            context_material="BOUNDED PROJECT EVIDENCE",
            output_contract="Return exact JSON.",
            retries=1
        )
        with patch.object(
            invocation, "resolve_provider", return_value=(OPENCODE, None)
        ), patch.object(invocation, "start_provider_process") as start:
            plan = invocation.prepare(request)

        start.assert_not_called()
        self.assertEqual(plan.task, "Draft project context.")
        self.assertEqual(plan.context_material, "BOUNDED PROJECT EVIDENCE")
        self.assertEqual(plan.output_contract, "Return exact JSON.")
        self.assertEqual(
            plan.provider_input,
            "Draft project context.\n\nBOUNDED PROJECT EVIDENCE\n\n"
            "OUTPUT CONTRACT\nReturn exact JSON."
        )

    def test_execute_uses_exact_plan_input_without_rebuilding(self):
        request = self.request("context_development", context_material="evidence")
        process = self.process(("answer",))
        with patch.object(
            invocation, "resolve_provider", return_value=(OPENCODE, None)
        ):
            plan = invocation.prepare(request)
        with patch.object(
            invocation, "start_provider_process", return_value=process
        ) as start:
            invocation.execute(plan)

        self.assertEqual(start.call_args.args[0][-1], plan.provider_input)

    def test_common_presenter_drives_freeform_and_structured_spinner_lifecycles(self):
        freeform = AIActivityPresenter("thinking")
        structured = AIActivityPresenter("developing context", stop_on_stream=False)
        freeform.spinner = Mock()
        structured.spinner = Mock()

        for event in ("preparing", "started", "streaming", "completed"):
            freeform(event)
            structured(event)

        freeform.spinner.start.assert_called_once_with()
        structured.spinner.start.assert_called_once_with()
        freeform.spinner.stop.assert_called_once_with(clear=True)
        structured.spinner.stop.assert_called_once_with(clear=True)


if __name__ == "__main__":
    unittest.main()
