import argparse
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from rotbot.commands import git as git_commands


class GitStartTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary_directory.name)
        (self.directory / "placeholder.txt").write_text(
            "placeholder\n", encoding="utf-8"
        )
        self.git_environment = {
            "GIT_AUTHOR_NAME": "Test User",
            "GIT_AUTHOR_EMAIL": "test@example.invalid",
            "GIT_COMMITTER_NAME": "Test User",
            "GIT_COMMITTER_EMAIL": "test@example.invalid"
        }

    def tearDown(self):
        self.temporary_directory.cleanup()

    def run_start(self, answers=("", ""), **patches):
        args = argparse.Namespace()
        environment = {**git_commands.os.environ, **self.git_environment}
        with patch("builtins.input", side_effect=iter(answers)), patch.dict(
            git_commands.os.environ, environment
        ), patch.object(git_commands, "rot_say") as rot_say, patches.get(
            "gh_available", patch.object(git_commands, "_gh_available", return_value=False)
        ), patches.get(
            "gh_authenticated", patch.object(git_commands, "_gh_authenticated", return_value=True)
        ), patches.get(
            "gh_create", patch.object(git_commands, "_create_gh_repository", return_value="")
        ):
            result = git_commands.git_start(args, working_directory=self.directory)
        messages = [call.args[0] for call in rot_say.call_args_list]
        return result, messages

    def git(self, *arguments):
        return subprocess.run(
            ["git", *arguments],
            cwd=self.directory,
            capture_output=True,
            text=True,
            check=False
        )

    def test_default_repository_name_comes_from_cwd_basename(self):
        directory = self.directory / "example-project"
        directory.mkdir()
        (directory / "placeholder.txt").write_text(
            "placeholder\n", encoding="utf-8"
        )
        self.directory = directory

        captured = {}

        def create(repository_name, visibility, working_directory):
            captured["name"] = repository_name
            captured["visibility"] = visibility
            return "https://github.com/owner/example-project"

        with patch("builtins.input", side_effect=iter(("", ""))), patch.dict(
            git_commands.os.environ, {**git_commands.os.environ, **self.git_environment}
        ), patch.object(
            git_commands, "_gh_available", return_value=True
        ), patch.object(
            git_commands, "_gh_authenticated", return_value=True
        ), patch.object(
            git_commands, "_create_gh_repository", side_effect=create
        ) as gh_create, patch.object(git_commands, "rot_say"):
            result = git_commands.git_start(
                argparse.Namespace(), working_directory=directory
            )

        self.assertEqual(result, 0)
        self.assertEqual(captured["name"], "example-project")
        self.assertEqual(captured["visibility"], "private")
        gh_create.assert_called_once()

    def test_custom_repository_name_and_public_visibility(self):
        captured = {}

        def create(repository_name, visibility, working_directory):
            captured["name"] = repository_name
            captured["visibility"] = visibility
            return "https://github.com/owner/custom-name"

        with patch("builtins.input", side_effect=iter(("custom-name", "public"))), patch.object(
            git_commands, "_gh_available", return_value=True
        ), patch.object(
            git_commands, "_gh_authenticated", return_value=True
        ), patch.object(
            git_commands, "_create_gh_repository", side_effect=create
        ), patch.object(git_commands, "rot_say"):
            result = git_commands.git_start(
                argparse.Namespace(), working_directory=self.directory
            )

        self.assertEqual(result, 0)
        self.assertEqual(captured["name"], "custom-name")
        self.assertEqual(captured["visibility"], "public")

    def test_private_is_the_default_visibility(self):
        captured = {}

        def create(repository_name, visibility, working_directory):
            captured["visibility"] = visibility
            return "https://github.com/owner/repo"

        with patch("builtins.input", side_effect=iter(("", ""))), patch.object(
            git_commands, "_gh_available", return_value=True
        ), patch.object(
            git_commands, "_gh_authenticated", return_value=True
        ), patch.object(
            git_commands, "_create_gh_repository", side_effect=create
        ), patch.object(git_commands, "rot_say"):
            git_commands.git_start(
                argparse.Namespace(), working_directory=self.directory
            )

        self.assertEqual(captured["visibility"], "private")

    def test_refusal_when_cwd_is_already_in_a_git_repository(self):
        self.git("init", "-q")

        with patch("builtins.input") as prompt, patch.object(
            git_commands, "rot_say"
        ) as rot_say:
            result = git_commands.git_start(
                argparse.Namespace(), working_directory=self.directory
            )

        self.assertEqual(result, 1)
        prompt.assert_not_called()
        self.assertIn(
            "already inside a Git repository",
            rot_say.call_args.args[0]
        )

    def test_successful_local_initialization_without_gh(self):
        result, messages = self.run_start()

        self.assertEqual(result, 0)
        self.assertEqual(messages[0], "✓ initialized git repository")
        self.assertEqual(messages[1], "✓ created initial commit")

        branch = self.git("branch", "--show-current")
        self.assertEqual(branch.stdout.strip(), "main")
        log = self.git("log", "--oneline")
        self.assertIn("Initial commit", log.stdout)

    def test_invalid_visibility_is_rejected(self):
        with patch("builtins.input", side_effect=iter(("", "secret"))), patch.object(
            git_commands, "rot_say"
        ) as rot_say:
            result = git_commands.git_start(
                argparse.Namespace(), working_directory=self.directory
            )

        self.assertEqual(result, 1)
        self.assertIn("Invalid visibility", rot_say.call_args.args[0])

    def test_gh_unavailable_keeps_local_repository(self):
        result, messages = self.run_start()

        self.assertEqual(result, 0)
        self.assertEqual(messages[0], "✓ initialized git repository")
        self.assertIn("! GitHub CLI unavailable", messages)
        self.assertIn("  remote repository was not created", messages)

        log = self.git("log", "--oneline")
        self.assertIn("Initial commit", log.stdout)

    def test_gh_unauthenticated_keeps_local_repository(self):
        result, messages = self.run_start(
            gh_available=patch.object(git_commands, "_gh_available", return_value=True),
            gh_authenticated=patch.object(git_commands, "_gh_authenticated", return_value=False)
        )

        self.assertEqual(result, 0)
        self.assertEqual(messages[0], "✓ initialized git repository")
        self.assertIn("! GitHub CLI is not authenticated", messages)
        self.assertIn("  remote repository was not created", messages)

        log = self.git("log", "--oneline")
        self.assertIn("Initial commit", log.stdout)

    def test_gh_remote_creation_failure_does_not_undo_local_repo(self):
        result, messages = self.run_start(
            gh_available=patch.object(git_commands, "_gh_available", return_value=True),
            gh_authenticated=patch.object(git_commands, "_gh_authenticated", return_value=True),
            gh_create=patch.object(git_commands, "_create_gh_repository", return_value=None)
        )

        self.assertEqual(result, 0)
        self.assertEqual(messages[0], "✓ initialized git repository")
        self.assertIn("! GitHub remote creation failed", messages)
        self.assertIn("  remote repository was not created", messages)

        log = self.git("log", "--oneline")
        self.assertIn("Initial commit", log.stdout)

    def test_mocked_successful_gh_repo_create(self):
        result, messages = self.run_start(
            gh_available=patch.object(git_commands, "_gh_available", return_value=True),
            gh_authenticated=patch.object(git_commands, "_gh_authenticated", return_value=True),
            gh_create=patch.object(
                git_commands,
                "_create_gh_repository",
                return_value="https://github.com/0xkamaji/example"
            )
        )

        self.assertEqual(result, 0)
        self.assertIn("✓ initialized git repository", messages)
        self.assertIn("✓ created initial commit", messages)
        self.assertIn("✓ created GitHub repository 0xkamaji/example", messages)
        self.assertIn("✓ added origin", messages)
        self.assertIn("✓ pushed main", messages)

    def test_gh_repo_create_uses_private_flag_by_default(self):
        with patch.object(
            git_commands.subprocess,
            "run",
            return_value=subprocess.CompletedProcess([], 0, stdout="")
        ) as run:
            output = git_commands._create_gh_repository(
                "repo-name", "private", self.directory
            )

        self.assertEqual(output, "")
        command = run.call_args.args[0]
        self.assertIn("--private", command)
        self.assertNotIn("--public", command)

    def test_gh_unavailable_helper_detects_missing_binary(self):
        with patch.object(git_commands.subprocess, "run", side_effect=FileNotFoundError):
            self.assertFalse(git_commands._gh_available())

    def test_gh_available_helper_and_auth_use_the_cli(self):
        with patch.object(
            git_commands.subprocess,
            "run",
            return_value=subprocess.CompletedProcess([], 0, stdout="gh version 2.x")
        ):
            self.assertTrue(git_commands._gh_available())
        with patch.object(
            git_commands.subprocess,
            "run",
            return_value=subprocess.CompletedProcess([], 0, stdout="logged in")
        ):
            self.assertTrue(git_commands._gh_authenticated())

    def test_gh_create_failure_returns_none(self):
        with patch.object(
            git_commands.subprocess,
            "run",
            return_value=subprocess.CompletedProcess([], 1, stdout="", stderr="error")
        ):
            self.assertIsNone(
                git_commands._create_gh_repository(
                    "repo-name", "private", self.directory
                )
            )

    def test_command_does_not_invoke_ai_functionality(self):
        from rotbot.agents import invocation

        with patch.object(invocation, "invoke") as invoke, patch.object(
            invocation, "execute"
        ) as execute, patch.object(git_commands, "_gh_available", return_value=False), patch(
            "builtins.input", side_effect=iter(("", ""))
        ), patch.dict(git_commands.os.environ, self.git_environment), patch.object(
            git_commands, "rot_say"
        ):
            result = git_commands.git_start(
                argparse.Namespace(), working_directory=self.directory
            )

        self.assertEqual(result, 0)
        invoke.assert_not_called()
        execute.assert_not_called()


if __name__ == "__main__":
    unittest.main()