import argparse
import unittest
from unittest.mock import Mock, patch

import parser as command_parser
import rotbot


class ParserDispatchTests(unittest.TestCase):
    ROUTES = (
        (
            ["ask", "what", "now", "--agent", "codex"],
            "ask_agent",
            {"question": ["what", "now"], "agent": "codex"}
        ),
        (["pull"], "git_pull", {"command": "pull"}),
        (
            [
                "push", "--review", "--message", "ship it",
                "--agent", "opencode", "--note", "check tests"
            ],
            "git_push",
            {
                "review": True,
                "message": "ship it",
                "agent": "opencode",
                "note": "check tests"
            }
        ),
        (
            ["wtf", "src", "--deep", "--note", "focus here"],
            "directory_report",
            {"target": "src", "deep": True, "note": "focus here"}
        ),
        (["sr", "status"], "sr_status", {"sr_command": "status"}),
        (
            ["sr", "context", "--refresh", "--agent", "codex"],
            "sr_context",
            {"refresh": True, "agent": "codex"}
        ),
        (
            ["sr", "diff", "--note", "production only"],
            "sr_diff",
            {"note": "production only"}
        ),
        (
            ["sr", "pull", "--review"],
            "sr_pull",
            {"review": True}
        ),
        (
            ["sr", "push", "--agent", "opencode"],
            "sr_push",
            {"agent": "opencode"}
        ),
        (
            ["sr", "publish", "--review", "--note", "dry run"],
            "sr_publish",
            {"review": True, "note": "dry run"}
        )
    )

    def assert_parse_error(self, argv):
        with patch.object(command_parser, "rot_say") as rot_say:
            with self.assertRaises(SystemExit) as raised:
                command_parser.parse_args(argv)
        self.assertEqual(raised.exception.code, 2)
        message = rot_say.call_args.args[0]
        self.assertIn("usage:", message)
        self.assertIn("Error:", message)

    def test_valid_commands_call_their_handlers(self):
        for argv, handler_name, expected_arguments in self.ROUTES:
            with self.subTest(argv=argv), patch.object(
                command_parser,
                handler_name
            ) as handler:
                args = command_parser.parse_args(argv)
                result = args.func(args)

                handler.assert_called_once_with(args)
                self.assertIs(result, handler.return_value)
                for name, expected in expected_arguments.items():
                    self.assertEqual(getattr(args, name), expected)

    def test_unknown_commands_are_rejected(self):
        for argv in (["unknown"], ["sr", "unknown"]):
            with self.subTest(argv=argv):
                self.assert_parse_error(argv)

    def test_missing_required_arguments_are_rejected(self):
        for argv in ([], ["ask"], ["sr"]):
            with self.subTest(argv=argv):
                self.assert_parse_error(argv)

    def test_malformed_options_are_rejected(self):
        for argv in (
            ["ask", "hello", "--agent", "invalid"],
            ["push", "--message"],
            ["pull", "--review"]
        ):
            with self.subTest(argv=argv):
                self.assert_parse_error(argv)

    def test_help_is_rendered_by_rot(self):
        for argv, expected in ((["-h"], "Signal Rot commands"), (["sr", "-h"], "status")):
            with self.subTest(argv=argv), patch.object(
                command_parser,
                "rot_say"
            ) as rot_say, self.assertRaises(SystemExit) as raised:
                command_parser.parse_args(argv)

            self.assertEqual(raised.exception.code, 0)
            self.assertIn("usage:", rot_say.call_args.args[0])
            self.assertIn(expected, rot_say.call_args.args[0])


class MainDispatchTests(unittest.TestCase):
    def test_main_returns_handler_exit_code(self):
        handler = Mock(return_value=7)
        args = argparse.Namespace(func=handler)

        with patch.object(rotbot, "parse_args", return_value=args):
            self.assertEqual(rotbot.main(), 7)

        handler.assert_called_once_with(args)

    def test_main_normalizes_non_integer_handler_result(self):
        handler = Mock(return_value=None)
        args = argparse.Namespace(func=handler)

        with patch.object(rotbot, "parse_args", return_value=args):
            self.assertEqual(rotbot.main(), 0)

        handler.assert_called_once_with(args)


if __name__ == "__main__":
    unittest.main()
