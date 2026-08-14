import argparse
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from rotbot.agents import invocation, runner
from rotbot.agents.config import OPENCODE
from rotbot.commands import debug
from rotbot.ui.debug import render_ai_debug_plan


class AIDebugTests(unittest.TestCase):
    def plan(self):
        available = SimpleNamespace(
            assistant=object(), user=object(), machine=None, project=object()
        )
        selected = SimpleNamespace(
            assistant=object(), user=None, machine=None, project=object()
        )
        return invocation.AIInvocationPlan(
            invocation_id="invocation",
            purpose="context_development",
            parent_command="context develop",
            provider=OPENCODE,
            provider_name="OpenCode",
            model=None,
            working_directory=Path("/tmp"),
            conversation_id="rotconv_example",
            provider_state=(SimpleNamespace(state_id="secret-session-id"),),
            available_persistent_context=available,
            selected_persistent_context=selected,
            available_conversation=(
                invocation.ConversationMessage("user", "one"),
                invocation.ConversationMessage("assistant", "two")
            ),
            selected_conversation=(
                invocation.ConversationMessage("assistant", "two"),
            ),
            task="Draft context",
            context_material="bounded 世界",
            provider_input="literal 世界 input\nunchanged",
            output_contract="Return JSON",
            retries=1,
            timeout=300,
            isolated=True,
            authority=None,
            context_sent=True,
            conversation_sent=False
        )

    def test_renderer_displays_real_plan_without_leaking_provider_state(self):
        plan = self.plan()
        rendered = render_ai_debug_plan(plan)

        self.assertIn("No provider was invoked.", rendered)
        self.assertIn("provider: OpenCode", rendered)
        self.assertIn("trust: external", rendered)
        self.assertIn("context view: egress", rendered)
        self.assertIn("purpose: context_development", rendered)
        self.assertIn("model: unresolved", rendered)
        self.assertIn("available: assistant, user, project", rendered)
        self.assertIn("selected: assistant, project", rendered)
        self.assertIn("available turns: 2", rendered)
        self.assertIn("selected turns: 1", rendered)
        self.assertIn("provider session state: present", rendered)
        self.assertNotIn("secret-session-id", rendered)
        self.assertIn("bounded 世界", rendered)
        self.assertIn("Return JSON", rendered)
        self.assertIn(
            "----- EXACT ROT -> PROVIDER INPUT -----\n\n"
            + plan.provider_input
            + "\n\n----- END ROT -> PROVIDER INPUT -----",
            rendered
        )
        self.assertIn(f"characters: {len(plan.provider_input)}", rendered)
        self.assertIn(
            f"bytes: {len(plan.provider_input.encode('utf-8'))}", rendered
        )

    def test_debug_ask_uses_shared_builder_and_only_prepares(self):
        request = invocation.AIRequest("ask", "ask", "question")
        operation = runner.AskOperation(request, object(), "question")
        args = argparse.Namespace(question=["question"], agent=None)

        with patch.object(
            debug, "build_ask_request", return_value=operation
        ) as builder, patch.object(
            debug, "prepare", return_value=self.plan()
        ) as prepare, patch.object(
            invocation, "execute"
        ) as execute, patch.object(
            invocation, "invoke"
        ) as invoke, patch.object(
            invocation, "start_provider_process"
        ) as start, patch("builtins.print"):
            result = debug.debug_ask(args)

        self.assertEqual(result, 0)
        builder.assert_called_once_with(args)
        prepare.assert_called_once_with(request)
        execute.assert_not_called()
        invoke.assert_not_called()
        start.assert_not_called()

    def test_debug_ask_uses_injected_context_without_inspecting_again(self):
        inspected = SimpleNamespace(cwd=Path("/interactive"))
        args = argparse.Namespace(
            question=["question"], agent=None, inspected_context=inspected
        )

        with patch.object(
            runner, "inspect_current_context"
        ) as inspect, patch.object(
            debug, "prepare", return_value=self.plan()
        ) as prepare, patch("builtins.print"):
            result = debug.debug_ask(args)

        self.assertEqual(result, 0)
        inspect.assert_not_called()
        request = prepare.call_args.args[0]
        self.assertIs(request.inspected_context, inspected)
        self.assertEqual(request.working_directory, inspected.cwd)

    def test_debug_ask_inspects_for_standalone_args(self):
        inspected = SimpleNamespace(cwd=Path("/standalone"))
        args = argparse.Namespace(question=["question"], agent=None)

        with patch.object(
            runner, "inspect_current_context", return_value=inspected
        ) as inspect, patch.object(
            debug, "prepare", return_value=self.plan()
        ), patch("builtins.print"):
            result = debug.debug_ask(args)

        self.assertEqual(result, 0)
        inspect.assert_called_once_with(bootstrap=False)

    def test_debug_ask_prints_and_sinks_the_same_rendered_text(self):
        request = invocation.AIRequest("ask", "ask", "question")
        operation = runner.AskOperation(request, object(), "question")
        sink = Mock()
        args = argparse.Namespace(question=["question"], agent=None, debug_sink=sink)

        with patch.object(
            debug, "build_ask_request", return_value=operation
        ), patch.object(debug, "prepare", return_value=self.plan()), patch.object(
            debug, "render_ai_debug_plan", return_value="exact debug text"
        ) as render, patch("builtins.print") as output:
            result = debug.debug_ask(args)

        self.assertEqual(result, 0)
        render.assert_called_once()
        output.assert_called_once_with("exact debug text")
        sink.assert_called_once_with("exact debug text", "debug-ask")

    def test_failed_debug_does_not_call_sink(self):
        sink = Mock()
        args = argparse.Namespace(question=["question"], agent=None, debug_sink=sink)
        with patch.object(
            debug, "build_ask_request", side_effect=debug.ContextInspectionError("failed")
        ), patch.object(debug, "rot_say"):
            result = debug.debug_ask(args)
        self.assertEqual(result, 2)
        sink.assert_not_called()

    def test_normal_ask_and_debug_share_request_builder(self):
        args = argparse.Namespace(question=["question"], agent=None)
        operation = runner.AskOperation(
            invocation.AIRequest("ask", "ask", "question"),
            SimpleNamespace(cwd=Path("/work")),
            "question"
        )
        result = invocation.AIResult(
            self.plan(), 127, "", 0, None, validation_error="unavailable"
        )

        with patch.object(
            runner, "build_ask_request", return_value=operation
        ) as normal_builder, patch.object(
            runner, "invoke", return_value=result
        ), patch.object(runner, "rot_say"):
            runner.ask_agent(args)
        with patch.object(
            debug, "build_ask_request", return_value=operation
        ) as debug_builder, patch.object(
            debug, "prepare", return_value=self.plan()
        ), patch("builtins.print"):
            debug.debug_ask(args)

        normal_builder.assert_called_once_with(args)
        debug_builder.assert_called_once_with(args)

    def test_debug_never_creates_conversation_storage(self):
        operation = runner.AskOperation(
            invocation.AIRequest("ask", "ask", "question"), object(), "question"
        )
        with patch.object(
            debug, "build_ask_request", return_value=operation
        ), patch.object(
            debug, "prepare", return_value=self.plan()
        ), patch(
            "rotbot.agents.runner.ConversationStore"
        ) as store, patch("builtins.print"):
            debug.debug_ask(argparse.Namespace(question=["question"], agent=None))

        store.assert_not_called()


if __name__ == "__main__":
    unittest.main()
