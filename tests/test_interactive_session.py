import argparse
from datetime import datetime
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, call, patch

from rotbot import __main__ as rotbot
from rotbot.cli import parser as command_parser
from rotbot.contexts import inspection
from rotbot.session import interactive
from rotbot.ui import interactive as interactive_ui


def inspected(cwd, project="rotbot", user="Kamaji", assistant="Rot", machine="laptop"):
    return inspection.InspectedContext(
        assistant, "assistant-id" if assistant else None,
        user, "user-id" if user else None,
        machine, "machine-id" if machine else None,
        project, "project-id" if project else None,
        Path(cwd),
        inspection.IdentificationSources(
            "local config" if assistant else "not configured",
            "local config" if user else "not configured",
            "local config" if machine else "not configured",
            "source binding" if project else "no matching project context"
        ),
        ()
    )


class RotSessionTests(unittest.TestCase):
    def setUp(self):
        self.original_cwd = Path.cwd()
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.first = self.root / "first"
        self.second = self.root / "second project"
        self.first.mkdir()
        self.second.mkdir()
        os.chdir(self.first)
        self.session = interactive.RotSession(
            datetime(2026, 8, 12, 12, 20),
            self.first,
            inspected(self.first)
        )

    def tearDown(self):
        os.chdir(self.original_cwd)
        self.temporary_directory.cleanup()

    def test_pwd_reports_session_directory(self):
        with patch.object(interactive, "rot_say") as rot_say:
            self.assertTrue(interactive.evaluate_input(self.session, "pwd"))

        rot_say.assert_called_once_with(str(self.first))

    def test_cd_changes_process_cwd_and_refreshes_project(self):
        refreshed = inspected(self.second, project="signalrot")
        with patch.object(
            interactive, "inspect_current_context", return_value=refreshed
        ) as inspect_context, patch.object(interactive, "rot_say") as rot_say:
            result = interactive.evaluate_input(
                self.session, f'cd "{self.second}"'
            )

        self.assertTrue(result)
        self.assertEqual(Path.cwd(), self.second)
        self.assertEqual(self.session.cwd, self.second)
        self.assertEqual(self.session.context.project, "signalrot")
        inspect_context.assert_called_once_with(cwd=self.second, bootstrap=False)
        self.assertIn("Project: signalrot", rot_say.call_args.args[0])

    def test_invalid_cd_does_not_change_directory_or_end_session(self):
        missing = self.root / "missing"
        with patch.object(interactive, "rot_say") as rot_say:
            result = interactive.evaluate_input(self.session, f"cd {missing}")

        self.assertTrue(result)
        self.assertEqual(Path.cwd(), self.first)
        self.assertEqual(self.session.cwd, self.first)
        self.assertIn("Directory does not exist", rot_say.call_args.args[0])

    def test_failed_context_refresh_restores_previous_directory(self):
        with patch.object(
            interactive,
            "inspect_current_context",
            side_effect=inspection.ContextInspectionError("broken context")
        ), patch.object(interactive, "rot_say"):
            result = interactive.evaluate_input(self.session, f'cd "{self.second}"')

        self.assertTrue(result)
        self.assertEqual(Path.cwd(), self.first)
        self.assertEqual(self.session.cwd, self.first)

    def test_existing_commands_use_shared_parser_and_handlers(self):
        cases = (
            ("git status", "git_status"),
            ("context inspect", "context_inspect")
        )
        for command, handler_name in cases:
            with self.subTest(command=command), patch.object(
                command_parser, handler_name, return_value=7
            ) as handler:
                self.assertTrue(interactive.evaluate_input(self.session, command))
            handler.assert_called_once()

    def test_incomplete_command_groups_show_next_steps(self):
        for command, expected in (
            ("git", ("pull", "push", "status")),
            ("machine", ("inspect",)),
            ("sr", ("status", "context", "diff", "pull", "push", "publish"))
        ):
            with self.subTest(command=command), patch.object(
                command_parser, "rot_say"
            ) as rot_say:
                result = interactive.evaluate_input(self.session, command)

            self.assertTrue(result)
            message = rot_say.call_args.args[0]
            self.assertIn(f"usage: rotbot {command}", message)
            for next_command in expected:
                self.assertIn(next_command, message)

    def test_command_group_help_flags_use_scoped_cli_help(self):
        for command, expected in (
            ("machine -h", "usage: rotbot machine"),
            ("git --help", "usage: rotbot git"),
            ("context -hv", "ROTBOT VERBOSE HELP"),
            ("sr --help-verbose", "ROTBOT VERBOSE HELP")
        ):
            with self.subTest(command=command), patch.object(
                command_parser, "rot_say"
            ) as rot_say:
                result = interactive.evaluate_input(self.session, command)

            self.assertTrue(result)
            self.assertIn(expected, rot_say.call_args.args[0])

    def test_failing_command_returns_to_session(self):
        with patch.object(
            command_parser, "git_status", return_value=1
        ) as git_status:
            result = interactive.evaluate_input(self.session, "git status")

        self.assertTrue(result)
        git_status.assert_called_once()

    def test_quoted_context_name_is_parsed_by_existing_cli(self):
        with patch.object(
            command_parser, "context_show", return_value=0
        ) as context_show:
            interactive.evaluate_input(
                self.session, 'context show "some context"'
            )

        self.assertEqual(context_show.call_args.args[0].name, "some context")

    def test_malformed_quoting_does_not_end_session(self):
        with patch.object(interactive, "rot_say") as rot_say:
            result = interactive.evaluate_input(self.session, 'context show "broken')

        self.assertTrue(result)
        self.assertIn("Could not parse command", rot_say.call_args.args[0])

    def test_all_normal_cli_command_families_use_shared_parser_and_handlers(self):
        cases = (
            ('ask "what next?"', "ask_agent"),
            ("pull", "git_pull"),
            ('push -m "ship it"', "git_push"),
            ("git status", "git_status"),
            ("wtf --deep .", "directory_report"),
            ("context list", "context_list"),
            ("machine inspect", "machine_inspect"),
            ("sr status", "sr_status")
        )
        for command, handler_name in cases:
            with self.subTest(command=command), patch.object(
                command_parser, handler_name, return_value=0
            ) as handler:
                result = interactive.evaluate_input(self.session, command)

            self.assertTrue(result)
            handler.assert_called_once()

    def test_unknown_prose_uses_parser_error_without_invoking_ai(self):
        with patch(
            "rotbot.agents.runner.ask_agent"
        ) as ask_agent, patch(
            "rotbot.agents.runner.stream_agent"
        ) as stream_agent, patch.object(command_parser, "rot_say") as rot_say:
            result = interactive.evaluate_input(
                self.session, "what should we work on next?"
            )

        self.assertTrue(result)
        ask_agent.assert_not_called()
        stream_agent.assert_not_called()
        self.assertIn("invalid choice", rot_say.call_args.args[0])

    def test_exit_and_quit_end_session(self):
        self.assertFalse(interactive.evaluate_input(self.session, "exit"))
        self.assertFalse(interactive.evaluate_input(self.session, "quit"))

    def test_clear_defers_header_redraw_to_prompt_loop(self):
        with patch.object(interactive, "clear_terminal") as clear:
            self.assertTrue(interactive.evaluate_input(self.session, "clear"))

        clear.assert_called_once_with()

    def test_clear_uses_active_header_controller(self):
        header = Mock()

        self.assertTrue(
            interactive.evaluate_input(self.session, "clear", header=header)
        )

        header.clear.assert_called_once_with(self.session)


class InteractiveLoopTests(unittest.TestCase):
    def test_no_argument_main_enters_interactive_session(self):
        args = argparse.Namespace(command=None)
        with patch.object(rotbot, "parse_args", return_value=args), patch(
            "rotbot.session.interactive.run_interactive", return_value=0
        ) as run_interactive:
            result = rotbot.main()

        self.assertEqual(result, 0)
        run_interactive.assert_called_once_with()

    def test_one_shot_main_still_calls_existing_handler(self):
        handler = Mock(return_value=4)
        with patch.object(
            rotbot, "parse_args", return_value=argparse.Namespace(func=handler)
        ), patch("rotbot.session.interactive.run_interactive") as run_interactive:
            result = rotbot.main()

        self.assertEqual(result, 4)
        handler.assert_called_once()
        run_interactive.assert_not_called()

    def test_exit_quit_and_eof_are_clean(self):
        session = Mock()
        for side_effect in (("exit",), ("quit",), (EOFError(),)):
            with self.subTest(side_effect=side_effect), patch.object(
                interactive.RotSession, "start", return_value=session
            ), patch.object(interactive, "SessionHeader") as header_type, patch(
                "builtins.input", side_effect=side_effect
            ):
                self.assertEqual(interactive.run_interactive(), 0)
            header_type.return_value.start.assert_called_once_with(session)
            header_type.return_value.stop.assert_called_once_with()

    def test_ctrl_c_at_prompt_returns_to_prompt(self):
        session = Mock()
        with patch.object(
            interactive.RotSession, "start", return_value=session
        ), patch.object(interactive, "SessionHeader"), patch(
            "builtins.input", side_effect=(KeyboardInterrupt(), "exit")
        ) as prompt, patch("builtins.print"):
            result = interactive.run_interactive()

        self.assertEqual(result, 0)
        self.assertEqual(prompt.call_count, 2)

    def test_command_failure_does_not_stop_loop(self):
        session = Mock()
        with patch.object(
            interactive.RotSession, "start", return_value=session
        ), patch.object(interactive, "SessionHeader") as header_type, patch(
            "builtins.input", side_effect=("git status", "exit")
        ), patch.object(interactive, "evaluate_input", side_effect=(True, False)) as evaluate:
            result = interactive.run_interactive()

        self.assertEqual(result, 0)
        header = header_type.return_value
        self.assertEqual(evaluate.call_args_list, [
            call(session, "git status", header=header),
            call(session, "exit", header=header)
        ])

    def test_header_is_refreshed_before_every_prompt(self):
        session = Mock()
        with patch.object(
            interactive.RotSession, "start", return_value=session
        ), patch.object(interactive, "SessionHeader") as header_type, patch(
            "builtins.input", side_effect=("pwd", "exit")
        ), patch.object(interactive, "evaluate_input", side_effect=(True, False)):
            result = interactive.run_interactive()

        self.assertEqual(result, 0)
        header = header_type.return_value
        header.start.assert_called_once_with(session)
        self.assertEqual(header.refresh.call_args_list, [call(session), call(session)])
        header.stop.assert_called_once_with()


class InteractiveUiTests(unittest.TestCase):
    def test_header_uses_resolved_context_and_time(self):
        session = interactive.RotSession(
            datetime(2026, 8, 12, 12, 20),
            Path("/work/rotbot"),
            inspected("/work/rotbot")
        )

        rendered = interactive_ui.render_session_header(
            session, now=datetime(2026, 8, 12, 12, 20), width=60
        )

        self.assertIn("ROT", rendered)
        self.assertIn("Kamaji", rendered)
        self.assertIn("Rot", rendered)
        self.assertIn("laptop", rendered)
        self.assertIn("project: rotbot", rendered)
        self.assertIn("cwd: /work/rotbot", rendered)
        self.assertIn("12:20 PM", rendered)
        self.assertIn("[x_o]", rendered)
        self.assertNotIn("ROTBOT", rendered)

    def test_header_and_status_support_missing_optional_context(self):
        session = interactive.RotSession(
            datetime(2026, 8, 12, 9, 5),
            Path("/tmp"),
            inspected("/tmp", None, None, None, None)
        )

        header = interactive_ui.render_session_header(session, width=72)
        status = interactive_ui.render_session_status(session)

        self.assertIn("user: unidentified", header)
        self.assertIn("project: none", header)
        self.assertIn("User:       unidentified", status)
        self.assertIn("Project:    none", status)

    def test_fixed_header_reserves_rows_and_restores_terminal(self):
        class TtyStream(io.StringIO):
            def isatty(self):
                return True

        stream = TtyStream()
        session = interactive.RotSession(
            datetime(2026, 8, 12, 12, 20),
            Path("/work/rotbot"),
            inspected("/work/rotbot")
        )
        header = interactive_ui.SessionHeader(stream)

        with patch.dict(os.environ, {"TERM": "xterm-256color"}), patch.object(
            header, "_terminal_size", return_value=(80, 24)
        ):
            header.start(session)
            header.refresh(session)
            header.stop()

        output = stream.getvalue()
        self.assertTrue(header.height > 0)
        self.assertIn(f"\033[{header.height + 1};24r", output)
        self.assertIn("\0337", output)
        self.assertIn("\0338", output)
        self.assertTrue(output.endswith("\033[r\033[999;1H"))

    def test_non_tty_header_prints_once_without_ansi(self):
        stream = io.StringIO()
        session = interactive.RotSession(
            datetime(2026, 8, 12, 12, 20),
            Path("/work/rotbot"),
            inspected("/work/rotbot")
        )
        header = interactive_ui.SessionHeader(stream)

        with patch.object(header, "_terminal_size", return_value=(80, 24)):
            header.start(session)
            initial = stream.getvalue()
            header.refresh(session)
            header.stop()

        self.assertFalse(header.fixed)
        self.assertNotIn("\033[", stream.getvalue())
        self.assertEqual(stream.getvalue(), initial)


if __name__ == "__main__":
    unittest.main()
