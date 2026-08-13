import argparse
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from rotbot.agents import invocation, runner
from rotbot.agents.config import OPENCODE
from rotbot.commands import debug
from rotbot.contexts import creation
from rotbot.contexts.evidence import ProjectDevelopmentEvidence
from rotbot.ui.debug import render_ai_debug_plan


class AIDebugTests(unittest.TestCase):
    def development_operation(self, identity_request, state_request=None):
        state_request = state_request or invocation.AIRequest(
            "context_state_development", "context develop", "state",
            context_material="state evidence", output_contract="state contract"
        )
        evidence = ProjectDevelopmentEvidence(
            "example", (), "Python application", (), (), (), (), (), None
        )
        return creation.ContextDevelopmentOperation(
            identity_request, state_request, "example", Path("/project"),
            Path("/context"), evidence
        )

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

    def test_debug_context_develop_uses_shared_read_only_builder(self):
        request = invocation.AIRequest(
            "context_development", "context develop", "draft",
            context_material="evidence", output_contract="contract"
        )
        operation = self.development_operation(request)
        args = argparse.Namespace(name="example", agent="opencode")

        with patch.object(
            debug, "build_context_develop_request", return_value=operation
        ) as builder, patch.object(
            debug, "prepare", return_value=self.plan()
        ) as prepare, patch.object(
            creation, "_atomic_replace_documents"
        ) as replace, patch.object(
            invocation, "start_provider_process"
        ) as start, patch("builtins.print"):
            result = debug.debug_context_develop(args)

        self.assertEqual(result, 0)
        builder.assert_called_once_with(args, debug.tempfile.gettempdir())
        self.assertEqual(prepare.call_count, 2)
        self.assertEqual(prepare.call_args_list[0].args[0], request)
        replace.assert_not_called()
        start.assert_not_called()

    def test_debug_context_develop_sinks_exact_combined_rendering(self):
        request = invocation.AIRequest("context_development", "context develop", "draft")
        operation = self.development_operation(request)
        sink = Mock()
        args = argparse.Namespace(name="example", agent=None, debug_sink=sink)
        with patch.object(
            debug, "build_context_develop_request", return_value=operation
        ), patch.object(debug, "prepare", return_value=self.plan()), patch.object(
            debug, "render_ai_debug_plan", side_effect=("identity plan", "state plan")
        ), patch("builtins.print") as output:
            debug.debug_context_develop(args)

        rendered = output.call_args.args[0]
        self.assertIn("CONTEXT DEVELOPMENT: IDENTITY", rendered)
        self.assertIn("identity plan", rendered)
        self.assertIn("CONTEXT DEVELOPMENT: STATE", rendered)
        self.assertIn("state plan", rendered)
        sink.assert_called_once_with(rendered, "debug-context-develop")

    def test_normal_and_debug_context_develop_share_request_builder(self):
        request = invocation.AIRequest(
            "context_development", "context develop", "draft",
            context_material="evidence", output_contract="contract"
        )
        operation = self.development_operation(request)
        args = argparse.Namespace(name="example", agent=None)
        failed = invocation.AIResult(
            self.plan(), 127, "", 0, None, validation_error="unavailable"
        )

        with patch.object(
            creation, "build_context_develop_request", return_value=operation
        ) as normal_builder, patch.object(
            creation, "load_match_definition", return_value=Mock()
        ), patch.object(
            creation, "_placeholder_documents", return_value={}
        ), patch.object(
            creation, "invoke", return_value=failed
        ), patch.object(creation, "rot_say"):
            creation.context_develop(args)
        with patch.object(
            debug, "build_context_develop_request", return_value=operation
        ) as debug_builder, patch.object(
            debug, "prepare", return_value=self.plan()
        ), patch("builtins.print"):
            debug.debug_context_develop(args)

        normal_builder.assert_called_once()
        self.assertIs(normal_builder.call_args.args[0], args)
        debug_builder.assert_called_once_with(args, debug.tempfile.gettempdir())

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

    def test_context_add_debug_is_explicitly_unsupported(self):
        with patch.object(debug, "rot_say") as say:
            result = debug.debug_context_add(Mock())
        self.assertEqual(result, 2)
        self.assertIn("not supported", say.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
