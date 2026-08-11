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

    def test_empty_agent_loads_default_key_and_verifies_remote(self):
        key = Path.home() / ".ssh" / "id_ed25519"
        responses = (
            self.completed(["ssh-add", "-l"], returncode=1),
            self.completed(["ssh-add", "-t", "1h", str(key)]),
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
            result = git_commands._preflight_ssh_push(
                Path("/repo"), "main", "origin/main"
            )

        self.assertIsInstance(result, dict)
        self.assertEqual(
            run.call_args_list[1].args[0],
            ["ssh-add", "-t", "1h", str(key)]
        )
        self.assertEqual(run.call_args_list[2].args[0][:2], ["git", "ls-remote"])
        self.assertIn("verified", rot_say.call_args.args[0])

    def test_key_load_failure_stops_before_remote_check(self):
        responses = (
            self.completed(["ssh-add", "-l"], returncode=1),
            self.completed(["ssh-add", "key"], returncode=1)
        )

        with patch.dict(git_commands.os.environ, {"GIT_SSH_COMMAND": "custom-ssh"}), patch.object(
            git_commands,
            "_push_remote_url",
            return_value="ssh://git@example.com/repo.git"
        ), patch.object(Path, "is_file", return_value=True), patch.object(
            git_commands.subprocess,
            "run",
            side_effect=responses
        ) as run, patch.object(git_commands, "rot_say") as rot_say:
            result = git_commands._preflight_ssh_push(
                Path("/repo"), "main", "origin/main"
            )

        self.assertIsNone(result)
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
        ), patch.object(Path, "is_file", return_value=False), patch.object(
            git_commands.subprocess,
            "run",
            side_effect=responses
        ), patch.object(git_commands, "rot_say") as rot_say:
            result = git_commands._preflight_ssh_push(
                Path("/repo"), "main", "origin/main"
            )

        self.assertIsNone(result)
        message = rot_say.call_args.args[0]
        self.assertIn("Permission denied (publickey)", message)
        self.assertIn("ssh-add", message)

    def test_custom_ssh_command_is_preserved_for_verification(self):
        responses = (
            self.completed(["ssh-add", "-l"], stdout="fingerprint key\n"),
            self.completed(["git", "ls-remote"], stdout="ref\n")
        )
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
            side_effect=responses
        ), patch.object(git_commands, "rot_say"):
            environment = git_commands._preflight_ssh_push(
                Path("/repo"), "main", "origin/main"
            )

        self.assertEqual(
            environment["GIT_SSH_COMMAND"],
            "ssh-wrapper --policy strict"
        )

    def test_unavailable_agent_uses_default_key_directly(self):
        key = Path.home() / ".ssh" / "id_ed25519"
        responses = (
            self.completed(["ssh-add", "-l"], returncode=2),
            self.completed(["git", "ls-remote"], stdout="ref\n")
        )
        with patch.object(
            git_commands,
            "_push_remote_url",
            return_value="git@example.com:repo.git"
        ), patch.object(Path, "is_file", return_value=True), patch.object(
            git_commands.subprocess,
            "run",
            side_effect=responses
        ), patch.object(git_commands, "rot_say"):
            environment = git_commands._preflight_ssh_push(
                Path("/repo"), "main", "origin/main"
            )

        self.assertIn(str(key), environment["GIT_SSH_COMMAND"])
        self.assertIn("IdentitiesOnly=yes", environment["GIT_SSH_COMMAND"])

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
