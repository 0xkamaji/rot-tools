import argparse
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from rotbot.commands import git as git_commands


class SshPushPreflightTests(unittest.TestCase):
    def completed(self, args, returncode=0, stdout="", stderr=""):
        return subprocess.CompletedProcess(args, returncode, stdout, stderr)

    def test_https_and_local_remotes_skip_ssh_preflight(self):
        for remote in (
            "https://github.com/example/repo.git",
            "/srv/git/repo.git"
        ):
            with self.subTest(remote=remote), patch.object(
                git_commands,
                "_push_remote_url",
                return_value=remote
            ), patch.object(git_commands.subprocess, "run") as run:
                self.assertIsInstance(
                    git_commands._preflight_ssh_push(
                        Path("/repo"), "main", "origin/main"
                    ),
                    dict
                )
                run.assert_not_called()

    def test_unresolved_push_remote_fails_closed(self):
        with patch.object(
            git_commands,
            "_push_remote_url",
            return_value=None
        ), patch.object(git_commands.subprocess, "run") as run, patch.object(
            git_commands,
            "rot_say"
        ) as rot_say:
            result = git_commands._preflight_ssh_push(
                Path("/repo"), "main", ""
            )

        self.assertIsNone(result)
        run.assert_not_called()
        self.assertIn("Could not determine", rot_say.call_args.args[0])

    def test_ssh_configured_identity_verifies_without_an_agent(self):
        with patch.object(
            git_commands,
            "_push_remote_url",
            return_value="git@github-work:example/repo.git"
        ), patch.object(
            git_commands.subprocess,
            "run",
            return_value=self.completed(["git", "ls-remote"], stdout="ref\n")
        ) as run, patch.object(git_commands, "rot_say") as rot_say:
            result = git_commands._preflight_ssh_push(
                Path("/repo"), "main", "origin/main"
            )

        self.assertIsInstance(result, dict)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.args[0][:2], ["git", "ls-remote"])
        environment = run.call_args.kwargs["env"]
        self.assertIn("BatchMode=yes", environment["GIT_SSH_COMMAND"])
        self.assertNotIn("-i", environment["GIT_SSH_COMMAND"].split())
        self.assertIn("verified", rot_say.call_args.args[0])

    def test_remote_authentication_failure_is_actionable(self):
        response = self.completed(
            ["git", "ls-remote"],
            returncode=128,
            stderr="Permission denied (publickey)."
        )

        with patch.object(
            git_commands,
            "_push_remote_url",
            return_value="git@github.com:example/repo.git"
        ), patch.object(
            git_commands.subprocess,
            "run",
            return_value=response
        ), patch.object(git_commands, "rot_say") as rot_say:
            result = git_commands._preflight_ssh_push(
                Path("/repo"), "main", "origin/main"
            )

        self.assertIsNone(result)
        message = rot_say.call_args.args[0]
        self.assertIn("Permission denied (publickey)", message)
        self.assertIn("SSH configuration", message)

    def test_custom_ssh_command_is_preserved_for_verification(self):
        with patch.dict(
            git_commands.os.environ,
            {"GIT_SSH_COMMAND": "ssh-wrapper --policy strict"}
        ), patch.object(
            git_commands,
            "_push_remote_url",
            return_value="git@example.com:repo.git"
        ), patch.object(
            git_commands.subprocess,
            "run",
            return_value=self.completed(["git", "ls-remote"], stdout="ref\n")
        ), patch.object(git_commands, "rot_say"):
            environment = git_commands._preflight_ssh_push(
                Path("/repo"), "main", "origin/main"
            )

        self.assertEqual(
            environment["GIT_SSH_COMMAND"],
            "ssh-wrapper --policy strict"
        )

    def test_push_remote_resolution_supports_remote_names_with_slashes(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory)
            subprocess.run(["git", "init", "-q"], cwd=repository, check=True)
            subprocess.run(
                [
                    "git", "remote", "add", "team/origin",
                    "git@example.com:team/repo.git"
                ],
                cwd=repository,
                check=True
            )
            subprocess.run(
                ["git", "config", "branch.main.remote", "team/origin"],
                cwd=repository,
                check=True
            )

            remote = git_commands._push_remote_url(
                repository,
                "main",
                "team/origin/main"
            )

        self.assertEqual(remote, "git@example.com:team/repo.git")


class GitPushPreflightTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.repository = Path(self.temporary_directory.name)
        subprocess.run(["git", "init", "-q"], cwd=self.repository, check=True)
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"],
            cwd=self.repository,
            check=True
        )
        subprocess.run(
            ["git", "config", "user.name", "Test User"],
            cwd=self.repository,
            check=True
        )
        tracked = self.repository / "tracked.txt"
        tracked.write_text("original\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=self.repository, check=True)
        subprocess.run(
            ["git", "commit", "-qm", "initial"],
            cwd=self.repository,
            check=True
        )
        tracked.write_text("changed\n", encoding="utf-8")

    def tearDown(self):
        self.temporary_directory.cleanup()

    def stage_until_commit(self, working_directory):
        arguments = argparse.Namespace(
            review=False,
            note=None,
            agent=None,
            message="test staging"
        )
        real_run = subprocess.run
        calls = []

        def recording_run(command, *args, **kwargs):
            calls.append((command, kwargs))
            if command == ["git", "commit", "-m", "test staging"]:
                return subprocess.CompletedProcess(command, 1)
            return real_run(command, *args, **kwargs)

        with patch("builtins.input", return_value="yes"), patch.object(
            git_commands,
            "_preflight_ssh_push",
            return_value={}
        ), patch.object(
            git_commands.subprocess,
            "run",
            side_effect=recording_run
        ), patch.object(git_commands, "rot_say"):
            result = git_commands.git_push(arguments, working_directory)

        self.assertEqual(result, 1)
        return calls

    def staged_paths(self, diff_filter=None):
        command = ["git", "diff", "--cached", "--name-only"]
        if diff_filter is not None:
            command.append(f"--diff-filter={diff_filter}")
        return subprocess.run(
            command,
            cwd=self.repository,
            capture_output=True,
            text=True,
            check=True
        ).stdout.splitlines()

    def test_staging_from_repository_root_stages_changes(self):
        calls = self.stage_until_commit(self.repository)

        self.assertIn("tracked.txt", self.staged_paths())
        add_call = next(call for call in calls if call[0] == ["git", "add", "--all"])
        self.assertEqual(Path(add_call[1]["cwd"]), self.repository)

    def test_staging_from_nested_directory_includes_repository_root_changes(self):
        nested = self.repository / "nested" / "directory"
        nested.mkdir(parents=True)
        outside = self.repository / "outside.txt"
        outside.write_text("outside nested directory\n", encoding="utf-8")

        self.stage_until_commit(nested)

        self.assertIn("outside.txt", self.staged_paths())

    def test_staging_includes_deleted_files(self):
        (self.repository / "tracked.txt").unlink()

        self.stage_until_commit(self.repository)

        self.assertEqual(self.staged_paths("D"), ["tracked.txt"])

    def test_staging_uses_a_non_shell_git_command(self):
        calls = self.stage_until_commit(self.repository)

        command, kwargs = next(
            call for call in calls if call[0] == ["git", "add", "--all"]
        )
        self.assertEqual(command, ["git", "add", "--all"])
        self.assertFalse(kwargs.get("shell", False))

    def test_failed_preflight_does_not_stage_or_commit_changes(self):
        args = argparse.Namespace(
            review=False,
            note=None,
            agent=None,
            message="should not commit"
        )

        with patch("builtins.input", return_value="yes"), patch.object(
            git_commands,
            "_preflight_ssh_push",
            return_value=None
        ) as preflight, patch.object(git_commands, "rot_say"):
            result = git_commands.git_push(args, working_directory=self.repository)

        self.assertEqual(result, 1)
        preflight.assert_called_once()
        status = subprocess.run(
            ["git", "status", "--short"],
            cwd=self.repository,
            capture_output=True,
            text=True,
            check=True
        )
        self.assertEqual(status.stdout, " M tracked.txt\n")
        log = subprocess.run(
            ["git", "log", "--oneline"],
            cwd=self.repository,
            capture_output=True,
            text=True,
            check=True
        )
        self.assertEqual(len(log.stdout.splitlines()), 1)

    def test_actual_push_uses_the_verified_environment(self):
        remote = self.repository / ".git" / "test-remote.git"
        subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
        subprocess.run(
            ["git", "remote", "add", "origin", str(remote)],
            cwd=self.repository,
            check=True
        )
        branch = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=self.repository,
            capture_output=True,
            text=True,
            check=True
        ).stdout.strip()
        subprocess.run(
            ["git", "push", "-qu", "origin", f"HEAD:{branch}"],
            cwd=self.repository,
            check=True
        )
        verified_environment = {**git_commands.os.environ, "ROT_TEST_VERIFIED": "1"}
        real_run = subprocess.run
        push_environments = []

        def recording_run(command, *args, **kwargs):
            if command == ["git", "push"]:
                push_environments.append(kwargs.get("env"))
            return real_run(command, *args, **kwargs)

        arguments = argparse.Namespace(
            review=False,
            note=None,
            agent=None,
            message="verified push"
        )
        with patch("builtins.input", return_value="yes"), patch.object(
            git_commands,
            "_preflight_ssh_push",
            return_value=verified_environment
        ), patch.object(
            git_commands.subprocess,
            "run",
            side_effect=recording_run
        ), patch.object(git_commands, "rot_say"):
            result = git_commands.git_push(
                arguments,
                working_directory=self.repository
            )

        self.assertEqual(result, 0)
        self.assertEqual(push_environments, [verified_environment])


if __name__ == "__main__":
    unittest.main()
