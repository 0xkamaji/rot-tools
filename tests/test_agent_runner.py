import argparse
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import call, Mock, patch

from rotbot.agents import runner
from rotbot.agents.config import CODEX
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

    def prompt_context(self):
        return prompt.PromptContext(
            None, None, None, None, "/work", "Codex"
        )

    def test_reports_response_time_after_successful_response(self):
        args = argparse.Namespace(question=["How", "long?"], agent="codex")

        with patch.object(runner, "_select_agent", return_value=CODEX), patch.object(
            runner, "inspect_current_context", return_value=self.inspected_context()
        ) as inspect, patch.object(
            runner, "resolve_prompt_context", return_value=self.prompt_context()
        ), patch.object(
            runner,
            "stream_agent",
            return_value=(0, "Answer\n", 2.34)
        ) as stream_agent, patch.object(runner, "rot_say") as rot_say:
            result = runner.ask_agent(args)

        self.assertEqual(result, 0)
        inspect.assert_called_once_with(bootstrap=False)
        call_args = stream_agent.call_args
        self.assertIn("<rotbot_context_instructions>", call_args.args[0])
        self.assertIn("<user_request>\n\nHow long?\n\n</user_request>", call_args.args[0])
        self.assertEqual(call_args.args[1], "Rot is still thinking...")
        self.assertEqual(call_args.kwargs, {
            "working_directory": Path("/work"),
            "display_question": "How long?",
            "agent_name": "codex"
        })
        self.assertEqual(rot_say.call_args_list[-1], call("Response received in 2.3s."))

    def test_reports_response_time_after_empty_success(self):
        args = argparse.Namespace(question="Anything?", agent=None)

        with patch.object(runner, "_select_agent", return_value=CODEX), patch.object(
            runner, "inspect_current_context", return_value=self.inspected_context()
        ), patch.object(
            runner, "resolve_prompt_context", return_value=self.prompt_context()
        ), patch.object(
            runner,
            "stream_agent",
            return_value=(0, "", 0.06)
        ), patch.object(runner, "rot_say") as rot_say:
            result = runner.ask_agent(args)

        self.assertEqual(result, 0)
        self.assertEqual(
            rot_say.call_args_list[-2:],
            [
                call("The AI agent returned no response."),
                call("Response received in 0.1s.")
            ]
        )

    def test_does_not_report_response_time_after_failure(self):
        args = argparse.Namespace(question="Anything?", agent=None)

        with patch.object(runner, "_select_agent", return_value=CODEX), patch.object(
            runner, "inspect_current_context", return_value=self.inspected_context()
        ), patch.object(
            runner, "resolve_prompt_context", return_value=self.prompt_context()
        ), patch.object(
            runner,
            "stream_agent",
            return_value=(1, "", 4.2)
        ), patch.object(runner, "rot_say") as rot_say:
            result = runner.ask_agent(args)

        self.assertEqual(result, 1)
        self.assertEqual(rot_say.call_args_list, [call("Let Rot think about that ...")])

    def test_missing_project_still_sends_context_prompt_and_original_display_question(self):
        args = argparse.Namespace(question="Original question", agent="codex")
        inspected = self.inspected_context()

        with patch.object(runner, "_select_agent", return_value=CODEX), patch.object(
            runner, "inspect_current_context", return_value=inspected
        ), patch.object(
            runner, "resolve_prompt_context", return_value=self.prompt_context()
        ), patch.object(
            runner, "stream_agent", return_value=(0, "Answer", 1.0)
        ) as stream_agent, patch.object(runner, "rot_say"):
            result = runner.ask_agent(args)

        self.assertEqual(result, 0)
        internal_prompt = stream_agent.call_args.args[0]
        self.assertNotEqual(internal_prompt, "Original question")
        self.assertNotIn("<project_context>", internal_prompt)
        self.assertEqual(
            stream_agent.call_args.kwargs["display_question"], "Original question"
        )

    def test_context_error_does_not_start_agent(self):
        args = argparse.Namespace(question="Question", agent="codex")
        with patch.object(runner, "_select_agent", return_value=CODEX), patch.object(
            runner,
            "inspect_current_context",
            side_effect=inspection.ContextInspectionError("broken context")
        ), patch.object(runner, "stream_agent") as stream_agent, patch.object(
            runner, "rot_say"
        ) as rot_say:
            result = runner.ask_agent(args)

        self.assertEqual(result, 2)
        stream_agent.assert_not_called()
        rot_say.assert_called_once_with("broken context")


class StreamAgentTests(unittest.TestCase):
    def test_streams_backend_output_with_separate_display_question(self):
        process = SimpleNamespace(
            stdout=iter(("First line\n", "\n", "Second line\n")),
            stderr=iter(()),
            wait=Mock(return_value=0),
            kill=Mock()
        )
        with patch.object(runner, "_select_agent", return_value=CODEX), patch.object(
            runner.subprocess, "Popen", return_value=process
        ) as popen, patch.object(runner, "rot_status"), patch.object(
            runner, "rot_break"
        ), patch.object(runner, "rot_output_start") as output_start, patch.object(
            runner, "rot_output_line"
        ) as output_line, patch.object(runner, "rot_output_end") as output_end:
            returncode, output, _elapsed = runner.stream_agent(
                "compiled prompt",
                "Thinking...",
                working_directory=Path("/work"),
                display_question="Original question",
                agent_name="codex"
            )

        self.assertEqual(returncode, 0)
        self.assertEqual(output, "First line\n\nSecond line\n")
        self.assertEqual(popen.call_args.kwargs["cwd"], Path("/work"))
        self.assertIn("compiled prompt", popen.call_args.args[0])
        output_start.assert_called_once_with("Original question")
        self.assertEqual(
            output_line.call_args_list,
            [call("First line"), call(""), call("Second line")]
        )
        output_end.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
