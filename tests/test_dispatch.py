import argparse
import unittest
from unittest.mock import Mock, patch

from rotbot import __main__ as rotbot
from rotbot.cli import parser as command_parser


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
        (["git", "pull"], "git_pull", {"git_command": "pull"}),
        (
            ["git", "status"],
            "git_status",
            {"git_command": "status", "fetch": False}
        ),
        (
            ["git", "status", "--fetch"],
            "git_status",
            {"git_command": "status", "fetch": True}
        ),
        (
            [
                "git", "push", "--review", "--message", "ship it",
                "--agent", "opencode", "--note", "check tests"
            ],
            "git_push",
            {
                "git_command": "push",
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
        (["context", "list"], "context_list", {"context_command": "list"}),
        (
            ["context", "show", "signalrot"],
            "context_show",
            {"context_command": "show", "name": "signalrot"}
        ),
        (
            ["context", "show", "signalrot", "--vision"],
            "context_show",
            {"context_command": "show", "name": "signalrot", "vision": True}
        ),
        (
            ["context", "bind"],
            "context_bind",
            {"context_command": "bind", "first": None, "second": None}
        ),
        (
            ["context", "bind", "."],
            "context_bind",
            {"first": ".", "second": None}
        ),
        (
            ["context", "bind", "signalrot", "/srv/site", "--as", "source"],
            "context_bind",
            {"first": "signalrot", "second": "/srv/site", "binding_type": "source"}
        ),
        (
            [
                "context", "bind", "signalrot", "/var/www/signalrot",
                "--as", "production"
            ],
            "context_bind",
            {"binding_type": "production"}
        ),
        (
            ["context", "add", "--agent", "codex"],
            "context_add",
            {
                "context_command": "add",
                "agent": "codex"
            }
        ),
        (
            ["context", "delete", "--name", "example"],
            "context_delete",
            {"context_command": "delete", "name": "example"}
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
        for argv in (
            [], ["ask"], ["git"], ["context"], ["context", "show"],
            ["context", "delete"],
            ["sr"]
        ):
            with self.subTest(argv=argv):
                self.assert_parse_error(argv)

    def test_malformed_options_are_rejected(self):
        for argv in (
            ["ask", "hello", "--agent", "invalid"],
            ["push", "--message"],
            ["pull", "--review"],
            ["context", "add", "example", "/srv/example"]
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

    def test_context_add_help_describes_interactive_creation(self):
        with patch.object(
            command_parser,
            "rot_say"
        ) as rot_say, self.assertRaises(SystemExit) as raised:
            command_parser.parse_args(["context", "add", "-h"])

        self.assertEqual(raised.exception.code, 0)
        message = rot_say.call_args.args[0]
        self.assertIn("Interactively create a project or person context", message)
        self.assertNotIn("NAME", message)
        self.assertNotIn("PATH", message)

    def test_verbose_help_renders_every_command(self):
        with patch.object(
            command_parser,
            "rot_say"
        ) as rot_say, self.assertRaises(SystemExit) as raised:
            command_parser.parse_args(["-hv"])

        self.assertEqual(raised.exception.code, 0)
        message = rot_say.call_args.args[0]
        self.assertIn("ROTBOT VERBOSE HELP", message)
        self.assertIn("COMMAND: rotbot git status", message)
        self.assertIn("COMMAND: rotbot context add", message)
        self.assertIn("COMMAND: rotbot context delete", message)
        self.assertIn("COMMAND: rotbot sr publish", message)
        self.assertIn("--help-verbose", message)
        self.assertEqual(message.count("-h, --help"), 1)
        self.assertEqual(message.count("-hv, --help-verbose"), 1)
        self.assertEqual(message.count("=" * 60), 14)
        self.assertLess(
            message.index("COMMAND: rotbot git status"),
            message.index("COMMAND: rotbot wtf")
        )

    def test_verbose_help_can_be_scoped_to_a_command_group(self):
        with patch.object(
            command_parser,
            "rot_say"
        ) as rot_say, self.assertRaises(SystemExit) as raised:
            command_parser.parse_args(["git", "--help-verbose"])

        self.assertEqual(raised.exception.code, 0)
        message = rot_say.call_args.args[0]
        self.assertIn("COMMAND: rotbot git status", message)
        self.assertNotIn("COMMAND: rotbot context", message)
        self.assertEqual(message.count("-h, --help"), 1)
        self.assertEqual(message.count("-hv, --help-verbose"), 1)
        self.assertEqual(message.count("=" * 60), 6)


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
