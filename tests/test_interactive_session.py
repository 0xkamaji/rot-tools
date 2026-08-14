import argparse
from contextlib import redirect_stdout
from datetime import datetime
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, call, patch

from rotbot import __main__ as rotbot
from rotbot.session import ai as session_ai
from rotbot.session.capabilities import AssistantCapabilityPolicy
from rotbot.cli import parser as command_parser
from rotbot.contexts import inspection
from rotbot.session import interactive
from rotbot.session.history import CommandHistory, HistoryError
from rotbot.session.state import SessionState, SessionStateError, SessionStateStore
from rotbot.ui import interactive as interactive_ui


def inspected(cwd, project="rotbot", user="Kamaji", assistant="Rot", machine="laptop"):
    return inspection.InspectedContext(
        assistant, "assistant-id" if assistant else None,
        user, "user-id" if user else None,
        machine, "machine-id" if machine else None,
        project, f"{project}-id" if project else None,
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
        self.state_store = Mock(spec=SessionStateStore)
        self.session = interactive.RotSession(
            datetime(2026, 8, 12, 12, 20),
            self.first,
            inspected(self.first),
            state_store=self.state_store
        )
        self.session.assistant_policy = AssistantCapabilityPolicy(
            work_enabled=True, valid=True
        )

    def tearDown(self):
        os.chdir(self.original_cwd)
        self.temporary_directory.cleanup()

    def test_pwd_reports_session_directory(self):
        with patch.object(interactive, "rot_say") as rot_say:
            self.assertTrue(interactive.evaluate_input(self.session, "pwd"))

        rot_say.assert_called_once_with(str(self.first))

    def test_start_bootstraps_context_and_saves_authoritative_state(self):
        context = inspected(self.first)
        store = Mock(spec=SessionStateStore)
        policy = AssistantCapabilityPolicy(work_enabled=True, valid=True)
        with patch.object(
            interactive, "inspect_current_context", return_value=context
        ) as inspect_context, patch.object(
            CommandHistory, "load"
        ), patch.object(
            interactive, "load_assistant_policy", return_value=policy
        ):
            session = interactive.RotSession.start(state_store=store)

        inspect_context.assert_called_once_with(cwd=self.first, bootstrap=True)
        store.save.assert_called_once_with(SessionState.from_inspected(context))
        self.assertIs(session.state_store, store)
        self.assertIs(session.context, context)
        self.assertIs(session.assistant_policy, policy)

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

    def test_bind_context_mutates_authoritative_context_and_saves(self):
        self.session.bind_context("user", "Alex", "alex-id")

        self.assertEqual(self.session.context.user, "Alex")
        self.assertEqual(self.session.context.user_id, "alex-id")
        self.assertEqual(
            self.session.context.identification_sources.user, "session binding"
        )
        self.state_store.save.assert_called_once_with(
            SessionState.from_inspected(self.session.context)
        )

    def test_explicit_user_binding_survives_refresh_and_directory_change(self):
        refreshed = inspected(self.second, project="signalrot", user="Default User")
        self.session.bind_context("user", "Alex", "alex-id")

        self.assertEqual(self.session.context.user, "Alex")
        self.assertEqual(self.session.context.user_id, "alex-id")
        self.assertEqual(
            self.session.context.identification_sources.user, "session binding"
        )

        with patch.object(
            interactive, "inspect_current_context", return_value=refreshed
        ):
            self.session.change_directory(self.second)

        self.assertEqual(self.session.context.user, "Alex")
        self.assertEqual(self.session.context.project, "signalrot")
        self.assertEqual(
            self.session.context.identification_sources.user, "session binding"
        )

    def test_failed_state_save_rolls_back_context_and_policy(self):
        previous_context = self.session.context
        previous_policy = self.session.assistant_policy
        self.state_store.save.side_effect = SessionStateError("failed")
        replacement_policy = AssistantCapabilityPolicy(work_enabled=False, valid=True)

        with patch.object(
            interactive, "load_assistant_policy", return_value=replacement_policy
        ), self.assertRaises(SessionStateError):
            self.session.bind_context("assistant", "Other", "other-id")

        self.assertIs(self.session.context, previous_context)
        self.assertIs(self.session.assistant_policy, previous_policy)

    def test_assistant_binding_revokes_existing_work_authority(self):
        self.session.authority_mode = "WORK"
        self.session.work_project_id = self.session.context.project_id
        replacement_policy = AssistantCapabilityPolicy(work_enabled=True, valid=True)

        with patch.object(
            interactive, "load_assistant_policy", return_value=replacement_policy
        ):
            self.session.bind_context("assistant", "Other", "other-id")

        self.assertEqual(self.session.authority_mode, "TALK")
        self.assertIsNone(self.session.work_project_id)

    def test_new_session_defaults_to_talk_and_ai_idle(self):
        self.assertEqual(self.session.authority_mode, "TALK")
        self.assertEqual(self.session.ai_status, "idle")

    def test_ai_status_uses_conversation_generation_state(self):
        chat = Mock(spec=session_ai.AIConversation)
        self.session.ai = chat
        chat.status = "idle"
        self.assertEqual(self.session.ai_status, "idle")
        chat.status = "thinking"
        self.assertEqual(self.session.ai_status, "thinking")
        chat.status = "active"
        self.assertEqual(self.session.ai_status, "active")

    def test_work_and_talk_switch_authority_without_starting_ai(self):
        with patch.object(interactive, "render_rot_response") as response:
            interactive.evaluate_input(self.session, "work")
            self.assertEqual(self.session.authority_mode, "WORK")
            self.assertEqual(self.session.ai_status, "idle")
            interactive.evaluate_input(self.session, "talk")

        self.assertEqual(self.session.authority_mode, "TALK")
        self.assertEqual(self.session.ai_status, "idle")
        self.assertEqual(response.call_args_list, [
            call(self.session, "Work mode enabled for rotbot."),
            call(self.session, "Talk mode enabled.")
        ])

    def test_mode_switches_do_not_mutate_assistant_policy(self):
        policy = AssistantCapabilityPolicy(work_enabled=True, valid=True)
        self.session.assistant_policy = policy

        self.assertTrue(self.session.enable_work())
        self.session.enable_talk()

        self.assertIs(self.session.assistant_policy, policy)
        self.assertEqual(self.session.authority_mode, "TALK")

    def test_invalid_policy_cannot_enable_work(self):
        self.session.assistant_policy = AssistantCapabilityPolicy(
            work_enabled=True, valid=False, error="invalid"
        )

        self.assertFalse(self.session.enable_work())
        self.assertEqual(self.session.authority_mode, "TALK")

    def test_talk_reduction_preserves_existing_conversation(self):
        chat = Mock(spec=session_ai.AIConversation)
        chat.remote_state = [Mock()]
        self.session.ai = chat
        self.session.enable_work()

        with patch.object(interactive, "render_rot_response"):
            interactive.evaluate_input(self.session, "talk")

        self.assertIs(self.session.ai, chat)
        self.assertEqual(self.session.ai_status, "active")
        self.assertEqual(self.session.authority_mode, "TALK")

    def test_natural_language_cannot_enable_work(self):
        with patch.object(self.session, "send_ai") as send_ai:
            interactive.evaluate_input(self.session, "let's work on this")

        self.assertEqual(self.session.authority_mode, "TALK")
        send_ai.assert_called_once_with("let's work on this")

    def test_work_requires_resolved_project_scope(self):
        self.session.context = inspected(self.first, project=None)
        with patch.object(interactive, "render_rot_response") as response:
            interactive.evaluate_input(self.session, "work")

        self.assertEqual(self.session.authority_mode, "TALK")
        response.assert_called_once_with(
            self.session,
            "Work mode requires an active project and assistant policy. "
            "Talk mode remains enabled."
        )

    def test_project_change_revokes_work_but_same_project_keeps_it(self):
        self.session.enable_work()
        same_project = inspected(self.second, project="rotbot")
        with patch.object(
            interactive, "inspect_current_context", return_value=same_project
        ), patch.object(interactive, "rot_say"), patch.object(
            interactive, "render_rot_response"
        ) as response:
            interactive.evaluate_input(self.session, f'cd "{self.second}"')
        self.assertEqual(self.session.authority_mode, "WORK")
        response.assert_not_called()

        os.chdir(self.first)
        self.session.cwd = self.first
        self.session.context = inspected(self.first, project="rotbot")
        changed = inspected(self.second, project="signalrot")
        with patch.object(
            interactive, "inspect_current_context", return_value=changed
        ), patch.object(interactive, "rot_say"), patch.object(
            interactive, "render_rot_response"
        ) as response:
            interactive.evaluate_input(self.session, f'cd "{self.second}"')

        self.assertEqual(self.session.authority_mode, "TALK")
        response.assert_called_once_with(
            self.session, "Work mode ended because the active project changed."
        )

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
            ("context show", "context_show")
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
            command_parser, "context_bind", return_value=0
        ) as context_bind:
            interactive.evaluate_input(
                self.session, 'context bind "some context"'
            )

        self.assertEqual(context_bind.call_args.args[0].first, "some context")

    def test_malformed_quoting_does_not_end_session(self):
        with patch.object(interactive, "rot_say") as rot_say:
            result = interactive.evaluate_input(self.session, 'context show "broken')

        self.assertTrue(result)
        self.assertIn("Could not parse input", rot_say.call_args.args[0])

    def test_all_normal_cli_command_families_use_shared_parser_and_handlers(self):
        cases = (
            ('ask "what next?"', "ask_agent"),
            ("pull", "git_pull"),
            ('push -m "ship it"', "git_push"),
            ("git status", "git_status"),
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

    def test_unknown_prose_routes_to_session_ai(self):
        with patch.object(self.session, "send_ai") as send_ai, patch(
            "rotbot.agents.runner.ask_agent"
        ) as ask_agent:
            result = interactive.evaluate_input(
                self.session, "what should we work on next?"
            )

        self.assertTrue(result)
        ask_agent.assert_not_called()
        send_ai.assert_called_once_with("what should we work on next?")

    def test_rot_and_shell_routes_never_invoke_ai(self):
        with patch.object(
            command_parser, "git_status", return_value=0
        ) as git_status, patch.object(
            interactive, "run_shell", return_value=0
        ) as run_shell, patch.object(self.session, "send_ai") as send_ai, patch(
            "rotbot.session.shell.shutil.which",
            side_effect=lambda name, path=None: "/bin/ls" if name == "ls" else None
        ):
            interactive.evaluate_input(self.session, "git status")
            interactive.evaluate_input(self.session, "ls -lah")

        git_status.assert_called_once()
        run_shell.assert_called_once_with("ls -lah", self.session.cwd)
        send_ai.assert_not_called()

    def test_overrides_force_ai_and_shell(self):
        with patch.object(self.session, "send_ai") as send_ai, patch.object(
            interactive, "run_shell", return_value=0
        ) as run_shell, patch.object(command_parser, "git_push") as git_push:
            interactive.evaluate_input(self.session, "? find a better design")
            interactive.evaluate_input(self.session, "!git push")

        send_ai.assert_called_once_with("find a better design")
        run_shell.assert_called_once_with("git push", self.session.cwd)
        git_push.assert_not_called()

    def test_question_override_uses_current_authority_without_elevation(self):
        chat = Mock(spec=session_ai.AIConversation)
        chat.remote_state = []
        chat.send.return_value = Mock(response="answer")
        self.session.ai = chat
        with patch.object(interactive, "render_rot_response"):
            interactive.evaluate_input(self.session, "? modify the resolver")

        chat.send.assert_called_once_with(
            "modify the resolver",
            self.session.context,
            self.session.cwd,
            authority="TALK",
            capability_state=self.session.capability_state,
            on_text=unittest.mock.ANY
        )
        self.assertEqual(self.session.authority_mode, "TALK")

    def test_export_and_unset_change_subsequent_shell_environment(self):
        original = os.environ.get("ROT_INTERACTIVE_TEST")
        try:
            interactive.evaluate_input(
                self.session, "export ROT_INTERACTIVE_TEST=present"
            )
            self.assertEqual(os.environ["ROT_INTERACTIVE_TEST"], "present")
            interactive.evaluate_input(self.session, "unset ROT_INTERACTIVE_TEST")
            self.assertNotIn("ROT_INTERACTIVE_TEST", os.environ)
        finally:
            if original is not None:
                os.environ["ROT_INTERACTIVE_TEST"] = original
            else:
                os.environ.pop("ROT_INTERACTIVE_TEST", None)

    def test_rot_typo_stays_deterministic_without_ai_or_shell(self):
        with patch.object(command_parser, "rot_say") as parser_say, patch.object(
            self.session, "send_ai"
        ) as send_ai, patch.object(interactive, "run_shell") as run_shell:
            result = interactive.evaluate_input(self.session, "git pusj")

        self.assertTrue(result)
        self.assertIn("invalid choice", parser_say.call_args.args[0])
        send_ai.assert_not_called()
        run_shell.assert_not_called()

    def test_shell_typo_suggests_without_ai_or_execution(self):
        with patch(
            "rotbot.session.router.available_executables", return_value=("cat",)
        ), patch(
            "rotbot.session.router.is_shell_executable", return_value=False
        ), patch.object(
            self.session, "send_ai"
        ) as send_ai, patch.object(
            interactive, "run_shell"
        ) as run_shell, patch.object(interactive, "rot_say") as rot_say:
            result = interactive.evaluate_input(self.session, "ct rotbot.py")

        self.assertTrue(result)
        self.assertIn("Did you mean `cat rotbot.py`?", rot_say.call_args.args[0])
        send_ai.assert_not_called()
        run_shell.assert_not_called()

    def test_first_ai_turn_uses_context_then_followup_reuses_conversation(self):
        chat = Mock(spec=session_ai.AIConversation)
        with patch.object(
            session_ai.AIConversation, "create", return_value=chat
        ) as create:
            self.session.send_ai("first question")
            self.session.send_ai("follow up")

        self.assertIs(self.session.ai, chat)
        create.assert_called_once()
        self.assertIsInstance(create.call_args.kwargs["store"], interactive.ConversationStore)
        self.assertEqual(chat.send.call_args_list, [
            call(
                "first question", self.session.context, self.session.cwd,
                authority="TALK", capability_state=self.session.capability_state,
                on_text=unittest.mock.ANY
            ),
            call(
                "follow up", self.session.context, self.session.cwd,
                authority="TALK", capability_state=self.session.capability_state,
                on_text=unittest.mock.ANY
            )
        ])

    def test_ai_success_becomes_active_and_uses_rot_speaker_renderer(self):
        chat = Mock(spec=session_ai.AIConversation)
        chat.remote_state = []

        def send(*args, **kwargs):
            chat.remote_state = [Mock()]
            return Mock(response="A conversational answer")

        chat.send.side_effect = send
        with patch.object(
            session_ai.AIConversation, "create", return_value=chat
        ), patch.object(interactive, "render_rot_response") as response:
            self.session.send_ai("Why?")

        self.assertEqual(self.session.ai_status, "active")
        response.assert_called_once_with(self.session, "A conversational answer")
        self.assertEqual(self.session.last_response.text, "A conversational answer")
        self.assertNotEqual(self.session.last_response.text, "Why?")

    def test_new_session_has_no_last_and_successive_ai_responses_replace_it(self):
        self.assertIsNone(self.session.last_response)
        self.assertIsNone(self.session.debug_response)
        chat = Mock(spec=session_ai.AIConversation)
        chat.send.side_effect = (Mock(response="first"), Mock(response="second"))
        self.session.ai = chat
        with patch.object(interactive, "render_rot_response"):
            self.session.send_ai("prompt one")
            self.session.send_ai("prompt two")

        self.assertEqual(self.session.last_response.text, "second")
        self.assertIsNone(self.session.debug_response)

    def test_failed_interrupted_and_empty_ai_do_not_replace_last(self):
        self.session.last_response = interactive.LastResponse("keep")
        chat = Mock(spec=session_ai.AIConversation)
        chat.send.side_effect = (
            interactive.ConversationError("failed"),
            KeyboardInterrupt(),
            Mock(response="")
        )
        self.session.ai = chat
        with patch.object(interactive, "rot_say"), patch.object(
            interactive, "StreamingRotResponse"
        ) as renderer:
            renderer.return_value.started = False
            for message in ("failed", "interrupted", "empty"):
                self.session.send_ai(message)

        self.assertEqual(self.session.last_response.text, "keep")

    def test_normal_ai_response_does_not_change_debug(self):
        previous = interactive.DebugResponse("keep debug", "debug-ask")
        self.session.debug_response = previous
        chat = Mock(spec=session_ai.AIConversation)
        chat.send.return_value = Mock(response="new answer")
        self.session.ai = chat
        with patch.object(interactive, "render_rot_response"):
            self.session.send_ai("question")

        self.assertEqual(self.session.last_response.text, "new answer")
        self.assertIs(self.session.debug_response, previous)

    def test_last_show_edit_save_and_learn_preserve_expected_ownership(self):
        self.session.last_response = interactive.LastResponse("original")
        header = Mock()
        with patch("builtins.print") as output:
            interactive.evaluate_input(self.session, "last show")
        output.assert_called_once_with("original")

        with patch.object(interactive, "edit_text", return_value="edited\n") as edit:
            interactive.evaluate_input(self.session, "last edit", header=header)
        edit.assert_called_once_with("original")
        self.assertEqual(self.session.last_response.text, "edited\n")
        self.assertTrue(self.session.last_response.edited)
        self.assertEqual(header.method_calls, [call.stop(), call.start(self.session)])

        before = self.session.last_response
        with patch.object(interactive, "save_text", return_value=Path("/saved")) as save:
            interactive.evaluate_input(self.session, "last save")
        save.assert_called_once_with("edited\n", category="responses")
        self.assertIs(self.session.last_response, before)

        with patch.object(
            interactive, "store_learned_text",
            side_effect=interactive.LearningError("unavailable")
        ) as learn, patch.object(interactive, "rot_say"):
            interactive.evaluate_input(self.session, "last learn project")
        learn.assert_called_once_with(
            "project", "edited\n", inspected=self.session.context, reference=None
        )
        self.assertIs(self.session.last_response, before)

    def test_last_show_without_response_fails_cleanly(self):
        with patch.object(interactive, "rot_say") as say:
            interactive.evaluate_input(self.session, "last show")
        self.assertIn("No AI response", say.call_args.args[0])

    def test_last_edit_failure_leaves_response_unchanged(self):
        self.session.last_response = interactive.LastResponse("keep")
        with patch.object(
            interactive, "edit_text", side_effect=interactive.LastResponseError("failed")
        ), patch.object(interactive, "rot_say"):
            interactive.evaluate_input(self.session, "last edit")
        self.assertEqual(self.session.last_response.text, "keep")
        self.assertFalse(self.session.last_response.edited)

    def test_last_ask_explicitly_sends_edited_text_and_replaces_last(self):
        self.session.last_response = interactive.LastResponse("edited response", edited=True)
        with patch.object(self.session, "send_ai", return_value="new response") as send:
            def update(message, header=None):
                self.session.last_response = interactive.LastResponse("new response")
                return "new response"
            send.side_effect = update
            interactive.evaluate_input(
                self.session, 'last ask "Condense this"'
            )

        message = send.call_args.args[0]
        self.assertIn("FOLLOW-UP INSTRUCTION\nCondense this", message)
        self.assertIn("PREVIOUS RESPONSE\nedited response", message)
        self.assertEqual(self.session.last_response.text, "new response")

    def test_last_ask_failure_preserves_previous_and_authority(self):
        previous = interactive.LastResponse("keep")
        self.session.last_response = previous
        self.session.authority_mode = "TALK"
        with patch.object(self.session, "send_ai", return_value=None) as send:
            interactive.evaluate_input(self.session, "last ask")

        self.assertIn("PREVIOUS RESPONSE\nkeep", send.call_args.args[0])
        self.assertIs(self.session.last_response, previous)
        self.assertEqual(self.session.authority_mode, "TALK")

    def test_normal_prompt_never_implicitly_includes_last(self):
        self.session.last_response = interactive.LastResponse("private scratch response")
        chat = Mock(spec=session_ai.AIConversation)
        chat.send.return_value = Mock(response="new")
        self.session.ai = chat
        with patch.object(interactive, "render_rot_response"):
            self.session.send_ai("ordinary question")

        self.assertEqual(chat.send.call_args.args[0], "ordinary question")
        self.assertNotIn("private scratch response", chat.send.call_args.args[0])

    def test_last_ask_uses_current_work_conversation_and_authority(self):
        self.session.assistant_policy = AssistantCapabilityPolicy(
            work_enabled=True, valid=True
        )
        self.assertTrue(self.session.enable_work())
        chat = Mock(spec=session_ai.AIConversation)
        chat.send.return_value = Mock(response="worked answer")
        self.session.ai = chat
        self.session.last_response = interactive.LastResponse("previous")
        with patch.object(interactive, "render_rot_response"):
            interactive.evaluate_input(self.session, 'last ask "continue"')

        self.assertIs(self.session.ai, chat)
        self.assertEqual(chat.send.call_args.kwargs["authority"], "WORK")
        self.assertEqual(chat.send.call_args.kwargs["capability_state"].mode, "WORK")
        self.assertEqual(self.session.authority_mode, "WORK")
        self.assertEqual(self.session.last_response.text, "worked answer")

    def test_debug_last_ask_prepares_real_conversation_request_without_mutation(self):
        chat = Mock(spec=session_ai.AIConversation)
        chat.id = "rotconv_current"
        chat.messages = ["user A", "assistant A"]
        chat.remote_state = ["provider state"]
        chat.context_fingerprint = "fingerprint"
        chat.context_version = 4
        chat.context_dirty = True
        chat.model = "model"
        request = Mock()
        chat.build_request.return_value = request
        self.session.ai = chat
        self.session.last_response = interactive.LastResponse("LAST response", edited=True)
        previous_last = self.session.last_response
        before = (
            chat.id, list(chat.messages), list(chat.remote_state),
            chat.context_fingerprint, chat.context_version, chat.context_dirty,
            chat.model, self.session.authority_mode, self.session.work_project_id
        )
        plan = Mock()

        with patch.object(interactive, "prepare", return_value=plan) as prepare, patch.object(
            interactive, "render_ai_debug_plan", return_value="rendered plan"
        ) as render, patch("builtins.print") as output, patch.object(
            interactive, "AIConversation"
        ) as conversation_type:
            interactive.evaluate_input(
                self.session, 'debug last ask "why is this the case?"'
            )

        message = chat.build_request.call_args.args[0]
        self.assertEqual(
            message,
            interactive.build_last_ask_message(
                "LAST response", "why is this the case?"
            )
        )
        self.assertEqual(chat.build_request.call_args.args[1:3], (
            self.session.context, self.session.cwd
        ))
        self.assertEqual(chat.build_request.call_args.kwargs["authority"], "TALK")
        self.assertEqual(chat.build_request.call_args.kwargs["capability_state"].mode, "TALK")
        prepare.assert_called_once_with(request)
        render.assert_called_once_with(plan)
        output.assert_called_once_with("rendered plan")
        self.assertEqual(self.session.debug_response.text, "rendered plan")
        self.assertEqual(self.session.debug_response.source, "debug-last-ask")
        conversation_type.create.assert_not_called()
        chat.send.assert_not_called()
        self.assertIs(self.session.last_response, previous_last)
        self.assertEqual(
            before,
            (
                chat.id, list(chat.messages), list(chat.remote_state),
                chat.context_fingerprint, chat.context_version, chat.context_dirty,
                chat.model, self.session.authority_mode, self.session.work_project_id
            )
        )

    def test_debug_register_show_edit_save_and_register_independence(self):
        last_response = interactive.LastResponse("LAST stays")
        self.session.last_response = last_response
        self.session.debug_response = interactive.DebugResponse(
            "exact debug", "debug-last-ask"
        )

        with patch("builtins.print") as output:
            interactive.evaluate_input(self.session, "debug show")
        output.assert_called_once_with("exact debug")

        header = Mock()
        with patch.object(interactive, "edit_text", return_value="edited debug") as edit:
            interactive.evaluate_input(self.session, "debug edit", header=header)
        edit.assert_called_once_with("exact debug")
        self.assertEqual(self.session.debug_response.text, "edited debug")
        self.assertTrue(self.session.debug_response.edited)
        self.assertIs(self.session.last_response, last_response)

        before = self.session.debug_response
        with patch.object(interactive, "save_text", return_value=Path("/saved")) as save:
            interactive.evaluate_input(self.session, "debug save")
        save.assert_called_once_with(
            "edited debug", category="debug", filename_hint="debug-last-ask"
        )
        self.assertIs(self.session.debug_response, before)
        self.assertIs(self.session.last_response, last_response)

    def test_debug_register_empty_and_editor_failure_preserve_state(self):
        with patch.object(interactive, "rot_say") as say:
            interactive.evaluate_input(self.session, "debug show")
        self.assertIn("No debug output", say.call_args.args[0])

        previous = interactive.DebugResponse("keep debug", "debug-ask")
        self.session.debug_response = previous
        with patch.object(
            interactive, "edit_text", side_effect=interactive.LastResponseError("failed")
        ), patch.object(interactive, "rot_say"):
            interactive.evaluate_input(self.session, "debug edit")
        self.assertIs(self.session.debug_response, previous)
        self.assertEqual(previous.text, "keep debug")
        self.assertFalse(previous.edited)

    def test_cli_debug_sink_replaces_edited_debug_but_not_last(self):
        previous_last = interactive.LastResponse("keep LAST")
        self.session.last_response = previous_last
        self.session.debug_response = interactive.DebugResponse(
            "edited old debug", "debug-ask", edited=True
        )

        def handler(parsed):
            parsed.debug_sink("new exact rendering", "debug-ask-rendered")
            return 0

        parsed = Mock(func=handler)
        with patch.object(interactive, "parse_args", return_value=parsed), patch(
            "rotbot.session.router.rot_command_names", return_value=("debug",)
        ):
            interactive.evaluate_input(self.session, "debug ask --render")

        self.assertEqual(self.session.debug_response.text, "new exact rendering")
        self.assertFalse(self.session.debug_response.edited)
        self.assertIs(self.session.last_response, previous_last)

    def test_rot_command_receives_session_binding_callback(self):
        captured = []
        parsed = Mock(func=lambda args: captured.append(args.bind_session_context) or 0)
        with patch.object(interactive, "parse_args", return_value=parsed):
            result = interactive._run_rot_command(["context", "bind"], self.session)

        self.assertEqual(result, 0)
        self.assertEqual(captured, [self.session.bind_context])

    def test_context_bind_then_show_displays_authoritative_user(self):
        user = Mock()
        user.name = "Alex"
        user.id = "alex-id"

        with patch(
            "rotbot.contexts.binding._load_session_context", return_value=user
        ), patch("rotbot.contexts.binding.rot_say"), patch(
            "rotbot.contexts.loader.rot_say"
        ) as show, patch.object(
            interactive, "inspect_current_context",
            return_value=inspected(self.first, user="Configured Default")
        ):
            interactive.evaluate_input(self.session, "context bind user Alex")
            interactive.evaluate_input(self.session, "context show")

        self.assertIn("User:       Alex", show.call_args.args[0])

    def test_rot_command_receives_current_session_context(self):
        captured = []
        parsed = Mock(func=lambda args: captured.append(args.inspected_context) or 0)
        with patch.object(interactive, "parse_args", return_value=parsed):
            result = interactive._run_rot_command(["ask", "question"], self.session)

        self.assertEqual(result, 0)
        self.assertEqual(captured, [self.session.context])

    def test_failed_or_interrupted_cli_debug_preserves_existing_debug(self):
        previous = interactive.DebugResponse("keep", "debug-ask")
        self.session.debug_response = previous
        failed = Mock(func=lambda _args: 2)
        with patch.object(interactive, "parse_args", return_value=failed), patch(
            "rotbot.session.router.rot_command_names", return_value=("debug",)
        ):
            interactive.evaluate_input(self.session, "debug ask failure")
        self.assertIs(self.session.debug_response, previous)

        interrupted = Mock(func=Mock(side_effect=KeyboardInterrupt))
        with patch.object(interactive, "parse_args", return_value=interrupted), patch(
            "rotbot.session.router.rot_command_names", return_value=("debug",)
        ), patch.object(interactive, "rot_say"):
            interactive.evaluate_input(self.session, "debug ask interrupted")
        self.assertIs(self.session.debug_response, previous)

    def test_debug_last_ask_no_last_fails_and_work_authority_is_preserved(self):
        with patch.object(interactive, "rot_say") as say:
            interactive.evaluate_input(self.session, "debug last ask")
        self.assertIn("No AI response", say.call_args.args[0])

        self.session.assistant_policy = AssistantCapabilityPolicy(
            work_enabled=True, valid=True
        )
        self.assertTrue(self.session.enable_work())
        chat = Mock(spec=session_ai.AIConversation)
        chat.build_request.return_value = Mock()
        self.session.ai = chat
        self.session.last_response = interactive.LastResponse("previous")
        with patch.object(interactive, "prepare", return_value=Mock()), patch.object(
            interactive, "render_ai_debug_plan", return_value="plan"
        ), patch("builtins.print"):
            interactive.evaluate_input(self.session, "debug last ask")

        self.assertEqual(chat.build_request.call_args.kwargs["authority"], "WORK")
        self.assertEqual(chat.build_request.call_args.kwargs["capability_state"].mode, "WORK")
        self.assertEqual(self.session.authority_mode, "WORK")

    def test_interrupted_debug_last_ask_preserves_existing_debug(self):
        previous = interactive.DebugResponse("keep", "debug-ask")
        self.session.debug_response = previous
        self.session.last_response = interactive.LastResponse("last")
        self.session.ai = Mock(spec=session_ai.AIConversation)
        self.session.ai.build_request.side_effect = KeyboardInterrupt

        with patch.object(interactive, "rot_say") as say:
            interactive.evaluate_input(self.session, "debug last ask")

        self.assertIs(self.session.debug_response, previous)
        self.assertIn("interrupted", say.call_args.args[0].lower())

    def test_real_last_ask_after_debug_still_executes_and_replaces_last(self):
        chat = Mock(spec=session_ai.AIConversation)
        chat.build_request.return_value = Mock()
        chat.send.return_value = Mock(response="new real response")
        self.session.ai = chat
        self.session.last_response = interactive.LastResponse("previous")
        with patch.object(interactive, "prepare", return_value=Mock()), patch.object(
            interactive, "render_ai_debug_plan", return_value="plan"
        ), patch("builtins.print"), patch.object(interactive, "render_rot_response"):
            interactive.evaluate_input(self.session, 'debug last ask "why"')
            interactive.evaluate_input(self.session, 'last ask "why"')

        chat.send.assert_called_once()
        self.assertEqual(self.session.last_response.text, "new real response")

    def test_shell_rot_and_debug_output_do_not_update_last(self):
        previous = interactive.LastResponse("keep")
        self.session.last_response = previous
        with patch.object(interactive, "run_shell", return_value=0), patch.object(
            interactive, "_run_rot_command", return_value=0
        ), patch("rotbot.session.router.is_shell_executable", return_value=True):
            interactive.evaluate_input(self.session, "ls -lah")
            interactive.evaluate_input(self.session, "pwd")
            interactive.evaluate_input(self.session, "debug ask question")

        self.assertIs(self.session.last_response, previous)

    def test_streamed_ai_text_does_not_refresh_fixed_header_mid_response(self):
        chat = Mock(spec=session_ai.AIConversation)
        chat.remote_state = []

        def send(*args, **kwargs):
            chat.status = "active"
            kwargs["on_text"]("visible response")
            return Mock(response="visible response")

        chat.send.side_effect = send
        header = Mock()
        renderer = Mock()
        renderer.started = True
        with patch.object(
            session_ai.AIConversation, "create", return_value=chat
        ), patch.object(
            interactive, "StreamingRotResponse", return_value=renderer
        ):
            self.session.send_ai("Why?", header=header)

        renderer.write.assert_called_once_with("visible response")
        self.assertEqual(header.refresh.call_args_list, [
            call(self.session), call(self.session)
        ])

    def test_shell_and_rot_commands_do_not_create_ai_conversation_storage(self):
        with patch.object(
            command_parser, "git_status", return_value=0
        ), patch.object(
            interactive, "run_shell", return_value=0
        ), patch.object(
            session_ai.AIConversation, "create"
        ) as create, patch(
            "rotbot.session.shell.shutil.which",
            side_effect=lambda name, path=None: "/bin/ls" if name == "ls" else None
        ):
            interactive.evaluate_input(self.session, "git status")
            interactive.evaluate_input(self.session, "ls -lah")

        create.assert_not_called()
        self.assertIsNone(self.session.ai)

    def test_ai_sessions_receives_current_rot_conversation_id(self):
        chat = Mock(spec=session_ai.AIConversation)
        chat.id = "rotconv_" + "a" * 32
        chat.remote_state = []
        self.session.ai = chat
        with patch.object(
            command_parser, "ai_sessions", return_value=0
        ) as sessions:
            interactive.evaluate_input(self.session, "ai sessions")

        args = sessions.call_args.args[0]
        self.assertEqual(args.active_conversation_id, chat.id)

    def test_cd_marks_rot_owned_ai_context_dirty(self):
        chat = Mock(spec=session_ai.AIConversation)
        self.session.ai = chat
        refreshed = inspected(self.second, project="signalrot")

        with patch.object(
            interactive, "inspect_current_context", return_value=refreshed
        ), patch.object(interactive, "rot_say"):
            interactive.evaluate_input(self.session, f'cd "{self.second}"')

        self.assertIs(self.session.ai, chat)
        chat.mark_context_dirty.assert_called_once_with()

    def test_public_context_inspect_no_longer_dispatches_or_refreshes(self):
        with patch.object(command_parser, "rot_say") as parser_say, patch.object(
            self.session, "refresh_context"
        ) as refresh:
            interactive.evaluate_input(self.session, "context inspect")

        self.assertIn("invalid choice", parser_say.call_args.args[0])
        refresh.assert_not_called()

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

    def test_history_displays_itself_and_recent_limit(self):
        for command in ("git status", "context inspect", "machine inspect", "history"):
            self.session.command_history.add(command)

        with patch.object(interactive, "rot_say") as rot_say:
            interactive.evaluate_input(self.session, "history 2")

        output = rot_say.call_args.args[0]
        self.assertNotIn("git status", output)
        self.assertNotIn("context inspect", output)
        self.assertIn("machine inspect", output)
        self.assertIn("history", output)

    def test_history_rejects_invalid_count(self):
        with patch.object(interactive, "rot_say") as rot_say:
            result = interactive.evaluate_input(self.session, "history nope")

        self.assertTrue(result)
        self.assertIn("Usage: history", rot_say.call_args.args[0])


class InteractiveLoopTests(unittest.TestCase):
    def test_history_load_failure_warns_but_session_starts(self):
        state_store = Mock(spec=SessionStateStore)
        with patch.object(
            interactive, "inspect_current_context", return_value=inspected(Path.cwd())
        ), patch.object(
            CommandHistory, "load", side_effect=HistoryError("unreadable")
        ), patch.object(interactive, "rot_say") as rot_say:
            session = interactive.RotSession.start(state_store=state_store)

        self.assertFalse(session.command_history.persistence_enabled)
        self.assertIn("could not be loaded", rot_say.call_args.args[0])
        state_store.save.assert_called_once_with(
            SessionState.from_inspected(session.context)
        )

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
        session.command_history.recent.return_value = []
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
        session.command_history.recent.return_value = []
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
        session.command_history.recent.return_value = []
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
        session.command_history.recent.return_value = []
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

    def test_submitted_commands_are_recorded_and_saved_on_exit(self):
        session = Mock()
        session.command_history.recent.return_value = []
        session.command_history.add.side_effect = (True, True)
        input_backend = Mock()
        input_backend.read.side_effect = ("git status", "exit")
        with patch.object(
            interactive.RotSession, "start", return_value=session
        ), patch.object(interactive, "SessionHeader"), patch.object(
            interactive, "interactive_input", return_value=input_backend
        ), patch.object(interactive, "evaluate_input", side_effect=(True, False)):
            result = interactive.run_interactive()

        self.assertEqual(result, 0)
        self.assertEqual(session.command_history.add.call_args_list, [
            call("git status"), call("exit")
        ])
        self.assertEqual(input_backend.record.call_args_list, [
            call("git status"), call("exit")
        ])
        session.command_history.save.assert_called_once_with()

    def test_eof_and_ctrl_c_save_without_recording_partial_input(self):
        session = Mock()
        session.command_history.recent.return_value = []
        input_backend = Mock()
        input_backend.read.side_effect = (KeyboardInterrupt(), EOFError())
        with patch.object(
            interactive.RotSession, "start", return_value=session
        ), patch.object(interactive, "SessionHeader"), patch.object(
            interactive, "interactive_input", return_value=input_backend
        ), patch("builtins.print"):
            result = interactive.run_interactive()

        self.assertEqual(result, 0)
        session.command_history.add.assert_not_called()
        session.command_history.save.assert_called_once_with()

    def test_history_failures_warn_without_stopping_session(self):
        session = Mock()
        session.command_history.recent.return_value = []
        session.command_history.save.side_effect = HistoryError("denied")
        input_backend = Mock()
        input_backend.read.side_effect = EOFError()
        with patch.object(
            interactive.RotSession, "start", return_value=session
        ), patch.object(interactive, "SessionHeader"), patch.object(
            interactive, "interactive_input", return_value=input_backend
        ), patch.object(interactive, "rot_say") as rot_say:
            result = interactive.run_interactive()

        self.assertEqual(result, 0)
        self.assertIn("could not be saved", rot_say.call_args.args[0])

    def test_deterministic_only_session_never_creates_ai_and_exit_closes_active_ai(self):
        session = Mock()
        session.ai = None
        session.command_history.recent.return_value = []
        input_backend = Mock()
        input_backend.read.side_effect = ("pwd", "exit")
        with patch.object(
            interactive.RotSession, "start", return_value=session
        ), patch.object(interactive, "SessionHeader"), patch.object(
            interactive, "interactive_input", return_value=input_backend
        ), patch.object(session_ai.AIConversation, "create") as chat_type, patch.object(
            interactive, "evaluate_input", side_effect=(True, False)
        ):
            result = interactive.run_interactive()

        self.assertEqual(result, 0)
        chat_type.assert_not_called()

        session.ai = Mock()
        input_backend.read.side_effect = EOFError()
        with patch.object(
            interactive.RotSession, "start", return_value=session
        ), patch.object(interactive, "SessionHeader"), patch.object(
            interactive, "interactive_input", return_value=input_backend
        ):
            self.assertEqual(interactive.run_interactive(), 0)
        session.ai.close.assert_called_once_with()


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
        self.assertIn("TALK · AI: idle", rendered)
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
        self.assertIn("Mode:       TALK", status)
        self.assertIn("AI:         idle", status)

    def test_prompt_uses_resolved_user_and_plain_fallback(self):
        class Tty:
            def isatty(self):
                return True

        session = interactive.RotSession(
            datetime(2026, 8, 12, 9, 5),
            Path("/tmp"),
            inspected("/tmp", user="Kamaji")
        )
        unresolved = interactive.RotSession(
            datetime(2026, 8, 12, 9, 5),
            Path("/tmp"),
            inspected("/tmp", user=None)
        )

        self.assertEqual(interactive_ui.interactive_prompt(session, Tty()), "kamaji ❯ ")
        self.assertEqual(interactive_ui.interactive_prompt(unresolved, io.StringIO()), "user > ")

    def test_rot_response_is_quiet_and_has_no_one_shot_framing(self):
        session = interactive.RotSession(
            datetime(2026, 8, 12, 9, 5),
            Path("/tmp"),
            inspected("/tmp", assistant="Rot")
        )
        output = io.StringIO()
        with redirect_stdout(output):
            interactive_ui.render_rot_response(session, "The answer.\n\nCode stays copyable.")

        rendered = output.getvalue()
        self.assertIn("\nrot [x_o]\nThe answer.", rendered)
        self.assertIn("Code stays copyable.", rendered)
        self.assertNotIn("ROT OUTPUT", rendered)
        self.assertNotIn("Question:", rendered)
        self.assertNotIn("Response:", rendered)

    def test_active_work_banner_and_status_use_authoritative_session_state(self):
        session = interactive.RotSession(
            datetime(2026, 8, 12, 9, 5),
            Path("/tmp"),
            inspected("/tmp")
        )
        session.assistant_policy = AssistantCapabilityPolicy(
            work_enabled=True, valid=True
        )
        session.enable_work()
        session.ai = Mock()
        session.ai.remote_state = [Mock()]
        session.ai.backend.name = "OpenCode"
        session.ai.backend.agent_name = "opencode"

        header = interactive_ui.render_session_header(session, width=72)
        status = interactive_ui.render_session_status(session)

        self.assertIn("WORK · AI: active", header)
        self.assertIn("Mode:       WORK", status)
        self.assertIn("AI:         active", status)
        self.assertIn("Backend:    OpenCode", status)

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
