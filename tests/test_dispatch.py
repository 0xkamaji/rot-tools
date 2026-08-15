import argparse
import unittest
from unittest.mock import Mock, patch

from rotbot import __main__ as rotbot
from rotbot.cli import parser as command_parser
from rotbot.commands.git import PUSH_CANCELLED


class ParserDispatchTests(unittest.TestCase):
    ROUTES = (
        (
            ["ask", "what", "now", "--agent", "codex"],
            "ask_agent",
            {"question": ["what", "now"], "agent": "codex"}
        ),
        (
            ["debug", "ask", "what", "now", "--agent", "codex"],
            "debug_ask",
            {"debug_command": "ask", "question": ["what", "now"], "agent": "codex"}
        ),
        (
            ["debug", "last", "ask", "why"],
            "debug_last_ask",
            {"debug_command": "last", "debug_last_command": "ask", "instruction": ["why"]}
        ),
        (
            ["context", "learn", "project", "exact", "fact"],
            "learn_command",
            {
                "context_command": "learn",
                "context_learn_target": "project",
                "learn_action": "append",
                "learn_target": "project",
                "text": ["exact", "fact"]
            }
        ),
        (
            ["context", "learn", "contact", "kamaji", "lives", "in", "Tokyo"],
            "learn_command",
            {
                "context_learn_target": "contact",
                "name": "kamaji",
                "learn_action": "append",
                "learn_target": "contact",
                "text": ["lives", "in", "Tokyo"]
            }
        ),
        (
            ["context", "edit", "user"],
            "learn_command",
            {
                "context_command": "edit",
                "context_edit_target": "user",
                "learn_action": "edit",
                "learn_target": "user"
            }
        ),
        (["debug", "show"], "debug_session_register", {"debug_command": "show"}),
        (["debug", "edit"], "debug_session_register", {"debug_command": "edit"}),
        (["debug", "save"], "debug_session_register", {"debug_command": "save"}),
        (["ai", "sessions"], "ai_sessions", {"ai_command": "sessions"}),
        (
            ["ai", "session", "show", "rotconv_abc"],
            "ai_session_show",
            {"ai_session_command": "show", "id": "rotconv_abc"}
        ),
        (
            ["ai", "session", "show"],
            "ai_session_show",
            {"ai_session_command": "show", "id": None}
        ),
        (["pull"], "git_pull", {"command": "pull"}),
        (
            ["push", "--message", "ship it"],
            "git_push",
            {"message": "ship it"}
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
            ["git", "push", "--message", "ship it"],
            "git_push",
            {"git_command": "push", "message": "ship it"}
        ),
        (
            ["git", "start"],
            "git_start",
            {"git_command": "start"}
        ),
        (
            ["git", "setup"],
            "git_setup",
            {"git_command": "setup"}
        ),
        (["context"], "context_menu", {"context_command": None}),
        (["context", "list"], "context_list", {"context_command": "list"}),
        (
            ["context", "show", "contact", "kamaji"],
            "context_show",
            {"context_command": "show", "target": "contact", "name": "kamaji"}
        ),
        (
            ["context", "show"],
            "context_show",
            {"context_command": "show", "target": None, "name": None}
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
            ["context", "bind", "user", "kamaji"],
            "context_bind",
            {"first": "user", "second": "kamaji", "binding_type": None}
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
            ["context", "add"],
            "context_add",
            {
                "context_command": "add",
                "context_type": None,
                "name": None
            }
        ),
        (
            ["context", "add", "machine"],
            "context_add",
            {"context_type": "machine", "name": None}
        ),
        (
            ["context", "add", "machine", "desktop"],
            "context_add",
            {"context_type": "machine", "name": "desktop"}
        ),
        (
            ["machine", "inspect"],
            "machine_inspect",
            {"command": "machine", "machine_command": "inspect"}
        ),
        (
            ["context", "delete", "example"],
            "context_delete",
            {"context_command": "delete", "name": "example"}
        ),
        (
            ["context", "delete"],
            "context_delete",
            {"context_command": "delete", "name": None}
        ),
        (
            ["context", "edit", "contact", "alex"],
            "learn_command",
            {"context_edit_target": "contact", "name": "alex"}
        ),
        (["sr", "status"], "sr_status", {"sr_command": "status"}),
        (
            ["sr", "context", "--full"],
            "sr_context",
            {"full": True}
        ),
        (
            ["sr", "diff"],
            "sr_diff",
            {"sr_command": "diff"}
        ),
        (
            ["sr", "pull"],
            "sr_pull",
            {"sr_command": "pull"}
        ),
        (
            ["sr", "push", "-m", "ship source"],
            "sr_push",
            {"message": "ship source"}
        ),
        (
            ["sr", "publish", "--message", "publish site"],
            "sr_publish",
            {"message": "publish site"}
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
        for argv in (
            ["unknown"], ["ai", "unknown"], ["ai", "session", "unknown"],
            ["sr", "unknown"], ["machine", "unknown"], ["context", "inspect"]
        ):
            with self.subTest(argv=argv):
                self.assert_parse_error(argv)

    def test_missing_required_arguments_are_rejected(self):
        for argv in (
            ["ask"]
        ):
            with self.subTest(argv=argv):
                self.assert_parse_error(argv)

    def test_no_arguments_are_reserved_for_interactive_mode(self):
        args = command_parser.parse_args([])

        self.assertIsNone(args.command)
        self.assertFalse(hasattr(args, "func"))

    def test_command_groups_show_scoped_next_steps(self):
        cases = (
            (["git"], ("pull", "push", "start", "status", "setup")),
            (["ai"], ("sessions", "session")),
            (["debug"], ("ask", "last")),
            (["ai", "session"], ("show",)),
            (["machine"], ("inspect",)),
            (["sr"], ("status", "context", "diff", "pull", "push", "publish"))
        )
        for argv, expected in cases:
            with self.subTest(argv=argv), patch.object(
                command_parser, "rot_say"
            ) as rot_say:
                args = command_parser.parse_args(argv)
                result = args.func(args)

            self.assertEqual(result, 0)
            message = rot_say.call_args.args[0]
            self.assertIn(f"usage: rotbot {argv[0]}", message)
            for command in expected:
                self.assertIn(command, message)

    def test_malformed_options_are_rejected(self):
        for argv in (
            ["ask", "hello", "--agent", "invalid"],
            ["push", "--message"],
            ["push", "--review"],
            ["git", "push", "--agent", "codex"],
            ["pull", "--review"],
            ["context", "add", "example", "/srv/example"],
            ["context", "add", "machine", "desktop", "extra"],
            ["context", "add", "machine", "desktop", "--inspect"],
            ["machine", "inspect", "--inspect"],
            ["context", "mod", "alex", "extra"],
            ["context", "delete", "example", "extra"],
            ["context", "show", "signalrot", "--vision"],
            ["wtf"],
            ["sr", "context", "--refresh"],
            ["sr", "diff", "--note", "production only"],
            ["sr", "pull", "--review"],
            ["sr", "push", "--agent", "opencode"],
            ["sr", "publish", "--note", "dry run"]
        ):
            with self.subTest(argv=argv):
                self.assert_parse_error(argv)

    def test_help_is_rendered_by_rot(self):
        cases = (
            (["-h"], ("Signal Rot commands",)),
            (["sr", "-h"], ("usage: rotbot sr", "publish")),
            (["machine", "-h"], ("usage: rotbot machine", "inspect")),
            (["git", "-h"], ("usage: rotbot git", "status")),
            (["git", "status", "-h"], ("usage: rotbot git status", "--fetch")),
            (["context", "show", "-h"], ("usage: rotbot context show",))
        )
        for argv, expected in cases:
            with self.subTest(argv=argv), patch.object(
                command_parser,
                "rot_say"
            ) as rot_say, self.assertRaises(SystemExit) as raised:
                command_parser.parse_args(argv)

            self.assertEqual(raised.exception.code, 0)
            message = rot_say.call_args.args[0]
            self.assertIn("usage:", message)
            for text in expected:
                self.assertIn(text, message)
            if argv == ["context", "show", "-h"]:
                self.assertNotIn("--vision", message)

    def test_context_add_help_describes_interactive_creation(self):
        with patch.object(
            command_parser,
            "rot_say"
        ) as rot_say, self.assertRaises(SystemExit) as raised:
            command_parser.parse_args(["context", "add", "-h"])

        self.assertEqual(raised.exception.code, 0)
        message = rot_say.call_args.args[0]
        self.assertIn(
            "Interactively create a project, person, or machine context",
            message
        )
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
        self.assertIn("COMMAND: rotbot ai session show", message)
        self.assertIn("COMMAND: rotbot context add", message)
        self.assertNotIn("COMMAND: rotbot context inspect", message)
        self.assertIn("COMMAND: rotbot machine inspect", message)
        self.assertIn("COMMAND: rotbot context delete", message)
        self.assertIn("COMMAND: rotbot context learn", message)
        self.assertIn("COMMAND: rotbot context edit", message)
        self.assertIn("COMMAND: rotbot sr publish", message)
        self.assertIn("--help-verbose", message)
        self.assertEqual(message.count("-h, --help"), 1)
        self.assertEqual(message.count("-hv, --help-verbose"), 1)
        self.assertEqual(message.count("=" * 60), 20)
        self.assertNotIn("COMMAND: rotbot wtf", message)

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
        self.assertEqual(message.count("=" * 60), 10)


class MainDispatchTests(unittest.TestCase):
    def test_main_returns_handler_exit_code(self):
        handler = Mock(return_value=7)
        args = argparse.Namespace(func=handler)

        with patch.object(rotbot, "parse_args", return_value=args):
            self.assertEqual(rotbot.main(), 7)

        handler.assert_called_once_with(args)

    def test_main_rejects_non_integer_handler_results(self):
        for result in (None, {}, True, False):
            with self.subTest(result=result):
                handler = Mock(return_value=result)
                args = argparse.Namespace(func=handler)

                with patch.object(rotbot, "parse_args", return_value=args):
                    self.assertEqual(rotbot.main(), 2)

                handler.assert_called_once_with(args)

    def test_main_preserves_successful_push_cancellation(self):
        handler = Mock(return_value=PUSH_CANCELLED)
        args = argparse.Namespace(func=handler)

        with patch.object(rotbot, "parse_args", return_value=args):
            self.assertEqual(rotbot.main(), 0)

        handler.assert_called_once_with(args)


if __name__ == "__main__":
    unittest.main()
