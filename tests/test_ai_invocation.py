from types import SimpleNamespace
from unittest.mock import Mock, patch
import unittest

from rotbot.agents import invocation
from rotbot.agents.config import OPENCODE
from rotbot.ui.ai import AIActivityPresenter


class AIInvocationTests(unittest.TestCase):
    def process(self, stdout=(), stderr=(), returncode=0):
        return SimpleNamespace(
            stdout=iter(stdout), stderr=iter(stderr),
            wait=Mock(return_value=returncode), kill=Mock()
        )

    def test_freeform_returns_stdout_and_emits_lifecycle(self):
        events = []
        lines = []
        process = self.process(("Hello\n",), ("> model banner\n",))
        request = invocation.AIInvocation("ask", "ask", "prompt", agent_name="opencode")
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
        request = invocation.AIInvocation(
            "context_development", "context develop", "prompt",
            agent_name="opencode", structured_output="integer", retries=1
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

    def test_retry_exhaustion_and_provider_failure_are_structured(self):
        request = invocation.AIInvocation(
            "context_development", "context develop", "prompt",
            agent_name="opencode", retries=1
        )
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
