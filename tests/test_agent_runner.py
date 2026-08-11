import argparse
import unittest
from unittest.mock import call, patch

from rotbot.agents import runner


class AskAgentTests(unittest.TestCase):
    def test_reports_response_time_after_successful_response(self):
        args = argparse.Namespace(question=["How", "long?"], agent="codex")

        with patch.object(
            runner,
            "stream_agent",
            return_value=(0, "Answer\n", 2.34)
        ) as stream_agent, patch.object(runner, "rot_say") as rot_say:
            result = runner.ask_agent(args)

        self.assertEqual(result, 0)
        stream_agent.assert_called_once_with(
            "How long?",
            "Rot is still thinking...",
            display_question="How long?",
            agent_name="codex"
        )
        self.assertEqual(rot_say.call_args_list[-1], call("Response received in 2.3s."))

    def test_reports_response_time_after_empty_success(self):
        args = argparse.Namespace(question="Anything?", agent=None)

        with patch.object(
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

        with patch.object(
            runner,
            "stream_agent",
            return_value=(1, "", 4.2)
        ), patch.object(runner, "rot_say") as rot_say:
            result = runner.ask_agent(args)

        self.assertEqual(result, 1)
        self.assertEqual(rot_say.call_args_list, [call("Let Rot think about that ...")])


if __name__ == "__main__":
    unittest.main()
