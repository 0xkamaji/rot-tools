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
                self.assertTrue(
                    git_commands._preflight_ssh_push(Path("/repo"), "origin/main")
                )
                run.assert_not_called()

    def test_empty_agent_loads_default_key_and_verifies_remote(self):
        key = Path.home() / ".ssh" / "id_ed25519"
        responses = (
            self.completed(["ssh-add", "-l"], returncode=1),
            self.completed(["ssh-add", str(key)]),
            self.completed(["git", "ls-remote"], stdout="ref\n")
        )

        with patch.object(
            git_commands,
            "_push_remote_url",
            return_value="git@github.com:example/repo.git"
        ), patch.object(Path, "is_file", return_value=True), patch.object(
            git_commands.subprocess,
            "run",
            side_effect=responses
        ) as run, patch.object(git_commands, "rot_say") as rot_say:
            result = git_commands._preflight_ssh_push(Path("/repo"), "origin/main")

        self.assertTrue(result)
        self.assertEqual(run.call_args_list[1].args[0], ["ssh-add", str(key)])
        self.assertEqual(run.call_args_list[2].args[0][:2], ["git", "ls-remote"])
        self.assertIn("verified", rot_say.call_args.args[0])

    def test_key_load_failure_stops_before_remote_check(self):
        responses = (
            self.completed(["ssh-add", "-l"], returncode=1),
            self.completed(["ssh-add", "key"], returncode=1)
        )

        with patch.object(
            git_commands,
            "_push_remote_url",
            return_value="ssh://git@example.com/repo.git"
        ), patch.object(Path, "is_file", return_value=True), patch.object(
            git_commands.subprocess,
            "run",
            side_effect=responses
        ) as run, patch.object(git_commands, "rot_say") as rot_say:
            result = git_commands._preflight_ssh_push(Path("/repo"), "origin/main")

        self.assertFalse(result)
        self.assertEqual(run.call_count, 2)
        self.assertIn("Could not load", rot_say.call_args.args[0])

    def test_remote_authentication_failure_is_actionable(self):
        responses = (
            self.completed(["ssh-add", "-l"], stdout="fingerprint key\n"),
            self.completed(
                ["git", "ls-remote"],
                returncode=128,
                stderr="Permission denied (publickey)."
            )
        )

        with patch.object(
            git_commands,
            "_push_remote_url",
            return_value="git@github.com:example/repo.git"
        ), patch.object(
            git_commands.subprocess,
            "run",
            side_effect=responses
        ), patch.object(git_commands, "rot_say") as rot_say:
            result = git_commands._preflight_ssh_push(Path("/repo"), "origin/main")

        self.assertFalse(result)
        message = rot_say.call_args.args[0]
        self.assertIn("Permission denied (publickey)", message)
        self.assertIn("ssh-add", message)


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
            return_value=False
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


if __name__ == "__main__":
    unittest.main()
