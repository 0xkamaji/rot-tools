import argparse
from contextlib import ExitStack
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from rotbot.commands import git as git_commands
from rotbot.contexts import accounts, entities, loader


USER_ID = "00000000-0000-4000-8000-000000000001"


class MachineConfigFake:
    def __init__(self):
        self.values = {
            "init.defaultBranch": "main",
            "user.name": "Kamaji",
            "user.email": "kamaji@example.invalid"
        }

    def get(self, key):
        return self.values.get(key, "")

    def set(self, key, value):
        if value == "":
            self.values.pop(key, None)
        else:
            self.values[key] = value
        return True


class GitStartTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary_directory.name)
        self.context_root = self.directory / "context"
        self.user = entities.build_user_context("kamaji", context_id=USER_ID)
        entities.create_entity_context(self.user, root=self.context_root)
        self.user_directory = entities.entity_directory(
            self.user, root=self.context_root
        )
        self.loader_patch = patch.object(loader, "CONTEXT_ROOT", self.context_root)
        self.loader_patch.start()
        self.addCleanup(self.loader_patch.stop)
        self.addCleanup(self.temporary_directory.cleanup)

        self.project_directory = Path(self.temporary_directory.name) / "example-project"
        self.project_directory.mkdir()
        (self.project_directory / "placeholder.txt").write_text(
            "placeholder\n", encoding="utf-8"
        )
        self.git_environment = {
            "GIT_AUTHOR_NAME": "Kamaji",
            "GIT_AUTHOR_EMAIL": "kamaji@example.invalid",
            "GIT_COMMITTER_NAME": "Kamaji",
            "GIT_COMMITTER_EMAIL": "kamaji@example.invalid"
        }

    def tearDown(self):
        self.loader_patch.stop()
        self.temporary_directory.cleanup()

    def write_accounts(self, account=None):
        account = account or accounts.AccountFile(
            git_name="Kamaji",
            git_email="kamaji@example.invalid",
            github_username="0xkamaji",
            github_default_visibility="private"
        )
        accounts.write_accounts(self.user_directory, account)
        return account

    def git(self, *arguments, directory=None):
        return subprocess.run(
            ["git", *arguments],
            cwd=directory or self.project_directory,
            capture_output=True,
            text=True,
            check=False
        )

    def enter_start_patches(
        self,
        stack,
        machine,
        ssh_username,
        remote_accessible,
        inputs,
        push_return=0
    ):
        stack.enter_context(
            patch("builtins.input", side_effect=input_side_effect(inputs))
        )
        stack.enter_context(
            patch.object(
                git_commands, "_github_ssh_username", return_value=ssh_username
            )
        )
        if isinstance(remote_accessible, list):
            stack.enter_context(
                patch.object(
                    git_commands, "_git_remote_accessible",
                    side_effect=remote_accessible
                )
            )
        else:
            stack.enter_context(
                patch.object(
                    git_commands, "_git_remote_accessible",
                    return_value=remote_accessible
                )
            )
        stack.enter_context(
            patch.object(
                git_commands, "_git_config_global_get", side_effect=machine.get
            )
        )
        stack.enter_context(
            patch.object(
                git_commands, "_git_config_global_set", side_effect=machine.set
            )
        )
        stack.enter_context(
            patch.object(
                git_commands,
                "get_local_context_bindings",
                return_value={"user": "kamaji"}
            )
        )
        real_run = git_commands.subprocess.run

        def guarded_run(command, *arguments, **kwargs):
            if tuple(command) == ("git", "push", "-u", "origin", "main"):
                return subprocess.CompletedProcess(command, push_return, "", "")
            return real_run(command, *arguments, **kwargs)

        stack.enter_context(
            patch.object(git_commands.subprocess, "run", side_effect=guarded_run)
        )
        stack.enter_context(patch.dict(git_commands.os.environ, self.git_environment))
        return stack.enter_context(patch.object(git_commands, "rot_say"))

    def run_start(
        self,
        machine=None,
        ssh_username="0xkamaji",
        remote_accessible=True,
        inputs=("", ""),
        push_return=0,
        directory=None
    ):
        machine = machine or MachineConfigFake()
        with ExitStack() as stack:
            rot_say = self.enter_start_patches(
                stack, machine, ssh_username, remote_accessible, inputs, push_return
            )
            result = git_commands.git_start(
                argparse.Namespace(),
                working_directory=directory or self.project_directory
            )
        messages = [item.args[0] for item in rot_say.call_args_list]
        return result, messages


def input_side_effect(values):
    remaining = list(values)

    def side_effect(prompt=""):
        return remaining.pop(0) if remaining else ""

    return side_effect


class GitStartWorkflowTests(GitStartTests):
    def test_default_repository_name_comes_from_cwd_basename(self):
        self.write_accounts()

        result, messages = self.run_start()

        self.assertEqual(result, 0)
        origin = self.git("remote", "get-url", "origin")
        self.assertEqual(
            origin.stdout.strip(),
            "git@github.com:0xkamaji/example-project.git"
        )

    def test_custom_repository_name_and_public_visibility(self):
        self.write_accounts()

        result, messages = self.run_start(
            remote_accessible=True,
            inputs=("custom-name", "public")
        )

        self.assertEqual(result, 0)
        origin = self.git("remote", "get-url", "origin")
        self.assertEqual(
            origin.stdout.strip(),
            "git@github.com:0xkamaji/custom-name.git"
        )

    def test_private_is_the_default_visibility(self):
        self.write_accounts(
            accounts.AccountFile(
                git_name="Kamaji",
                git_email="kamaji@example.invalid",
                github_username="0xkamaji",
                github_default_visibility=""
            )
        )

        result, messages = self.run_start(
            remote_accessible=[False, True],
            inputs=("", "", "")
        )

        self.assertEqual(result, 0)
        self.assertIn("Visibility:  private", "\n".join(messages))

    def test_author_and_username_come_from_user_context(self):
        self.write_accounts()

        result, messages = self.run_start()

        self.assertEqual(result, 0)
        index = messages.index("Git author:")
        self.assertEqual(messages[index + 1], "  Kamaji <kamaji@example.invalid>")
        github_index = messages.index("GitHub account:")
        self.assertEqual(messages[github_index + 1], "  0xkamaji")
        self.assertNotEqual("Kamaji", "0xkamaji")

    def test_refusal_when_cwd_is_already_in_a_git_repository(self):
        self.git("init", "-q")

        with patch("builtins.input") as prompt, patch.object(
            git_commands, "rot_say"
        ) as rot_say:
            result = git_commands.git_start(
                argparse.Namespace(), working_directory=self.project_directory
            )

        self.assertEqual(result, 1)
        prompt.assert_not_called()
        self.assertIn(
            "already inside a Git repository",
            rot_say.call_args.args[0]
        )

    def test_incomplete_accounts_prompt_and_configure(self):
        setup_written = False

        def fake_setup(person, user_directory):
            nonlocal setup_written
            accounts.write_accounts(
                user_directory,
                accounts.AccountFile(
                    git_name="Kamaji",
                    git_email="kamaji@example.invalid",
                    github_username="0xkamaji",
                    github_default_visibility="private"
                )
            )
            setup_written = True
            return 0

        with ExitStack() as stack:
            machine = MachineConfigFake()
            rot_say = self.enter_start_patches(
                stack, machine, "0xkamaji", True,
                ("y", "", ""), push_return=0
            )
            stack.enter_context(
                patch.object(git_commands, "_setup_flow", side_effect=fake_setup)
            )
            result = git_commands.git_start(
                argparse.Namespace(), working_directory=self.project_directory
            )
        messages = [item.args[0] for item in rot_say.call_args_list]

        self.assertEqual(result, 0)
        self.assertTrue(setup_written)
        self.assertIn("Git setup is incomplete for this Rot user.", messages)

    def test_incomplete_accounts_declined_stops_before_init(self):
        with ExitStack() as stack:
            rot_say = self.enter_start_patches(
                stack, MachineConfigFake(), "0xkamaji", True, ("n",)
            )
            result = git_commands.git_start(
                argparse.Namespace(), working_directory=self.project_directory
            )
        messages = [item.args[0] for item in rot_say.call_args_list]

        self.assertEqual(result, 1)
        self.assertIn("No Git repository was created.", messages)
        self.assertFalse((self.project_directory / ".git").exists())

    def test_ssh_missing_stops_before_init(self):
        self.write_accounts()

        result, messages = self.run_start(ssh_username=None)

        self.assertEqual(result, 1)
        self.assertIn(
            "GitHub SSH authentication is not configured on this machine.",
            "\n".join(messages)
        )
        self.assertFalse((self.project_directory / ".git").exists())

    def test_ssh_username_mismatch_stops_before_init(self):
        self.write_accounts()

        result, messages = self.run_start(ssh_username="someone-else")

        self.assertEqual(result, 1)
        self.assertIn("GitHub account mismatch.", messages)
        self.assertFalse((self.project_directory / ".git").exists())

    def test_successful_local_initialization_and_push(self):
        self.write_accounts()

        result, messages = self.run_start()

        self.assertEqual(result, 0)
        self.assertIn("✓ initialized git repository", messages)
        self.assertIn("✓ created initial commit", messages)
        self.assertIn("✓ added origin", messages)
        self.assertIn("✓ pushed main", messages)
        branch = self.git("branch", "--show-current")
        self.assertEqual(branch.stdout.strip(), "main")
        log = self.git("log", "--oneline")
        self.assertIn("Initial commit", log.stdout)
        self.assertIn("0xkamaji/example-project", origin_string(self))

    def test_existing_github_repository_is_detected(self):
        self.write_accounts()

        result, messages = self.run_start(remote_accessible=True)

        self.assertEqual(result, 0)
        self.assertIn(
            "✓ found existing GitHub repository 0xkamaji/example-project",
            messages
        )

    def test_manual_creation_prompt_waits_for_enter(self):
        self.write_accounts()

        result, messages = self.run_start(
            remote_accessible=[False, True],
            inputs=("", "", "")
        )

        self.assertEqual(result, 0)
        self.assertIn("Do not initialize it with a README", "\n".join(messages))
        self.assertIn(
            "✓ found GitHub repository 0xkamaji/example-project", "\n".join(messages)
        )

    def test_manual_creation_cancel_leaves_no_repository(self):
        self.write_accounts()

        result, messages = self.run_start(
            remote_accessible=False,
            inputs=("", "", "q")
        )

        self.assertEqual(result, 1)
        self.assertIn("Cancelled. No Git repository was created.", messages)
        self.assertFalse((self.project_directory / ".git").exists())

    def test_manual_creation_failure_leaves_no_repository(self):
        self.write_accounts()

        result, messages = self.run_start(
            remote_accessible=False,
            inputs=("", "", "")
        )

        self.assertEqual(result, 1)
        self.assertIn("Could not verify GitHub repository:", messages)
        self.assertFalse((self.project_directory / ".git").exists())

    def test_local_initialization_failure_rolls_back_only_git(self):
        self.write_accounts()

        with ExitStack() as stack:
            machine = MachineConfigFake()
            rot_say = self.enter_start_patches(
                stack, machine, "0xkamaji", True, ("", ""), push_return=0
            )
            real_run = git_commands.subprocess.run

            def failing_commit(command, *arguments, **kwargs):
                if tuple(command) == ("git", "commit", "-m", "Initial commit"):
                    return subprocess.CompletedProcess(command, 128, "", "")
                return real_run(command, *arguments, **kwargs)

            stack.enter_context(
                patch.object(
                    git_commands.subprocess, "run", side_effect=failing_commit
                )
            )
            result = git_commands.git_start(
                argparse.Namespace(),
                working_directory=self.project_directory
            )
        messages = [item.args[0] for item in rot_say.call_args_list]

        self.assertEqual(result, 128)
        self.assertIn("! local initialization failed and was rolled back", messages)
        self.assertFalse((self.project_directory / ".git").exists())
        self.assertTrue((self.project_directory / "placeholder.txt").exists())
        self.assertTrue(
            len(list(self.project_directory.iterdir())) == 1
        )

    def test_push_failure_keeps_local_repository(self):
        self.write_accounts()

        result, messages = self.run_start(push_return=5)

        self.assertEqual(result, 5)
        self.assertIn("! push failed", messages)
        self.assertIn("  local repository retained", messages)
        self.assertIn(
            "  remote: git@github.com:0xkamaji/example-project.git", messages
        )
        self.assertTrue((self.project_directory / ".git").exists())
        origin = self.git("remote", "get-url", "origin")
        self.assertIn("0xkamaji/example-project.git", origin.stdout)

    def test_invalid_visibility_is_rejected(self):
        self.write_accounts()

        result, messages = self.run_start(
            inputs=("", "secret")
        )

        self.assertEqual(result, 1)
        self.assertIn("Invalid visibility: secret", messages)
        self.assertFalse((self.project_directory / ".git").exists())

    def test_command_does_not_invoke_ai_functionality(self):
        self.write_accounts()
        from rotbot.agents import invocation

        with ExitStack() as stack:
            machine = MachineConfigFake()
            self.enter_start_patches(
                stack, machine, "0xkamaji", True, ("", "")
            )
            invoke = stack.enter_context(patch.object(invocation, "invoke"))
            execute = stack.enter_context(patch.object(invocation, "execute"))
            result = git_commands.git_start(
                argparse.Namespace(), working_directory=self.project_directory
            )

        self.assertEqual(result, 0)
        invoke.assert_not_called()
        execute.assert_not_called()

    def test_initialization_does_not_touch_github_cli(self):
        self.write_accounts()

        result, messages = self.run_start()

        self.assertEqual(result, 0)
        self.assertTrue(all("gh " not in message for message in messages))


class GitStartAliasTests(GitStartTests):
    def enter_alias_patches(
        self,
        stack,
        machine,
        ssh_host,
        hosts_tested,
        remote_accessible=True,
        push_return=0
    ):
        stack.enter_context(
            patch.object(
                git_commands, "_machine_ssh_host",
                side_effect=lambda: ssh_host
            )
        )

        def ssh_side_effect(host="github.com"):
            hosts_tested.append(host)
            return "0xkamaji"

        stack.enter_context(
            patch.object(
                git_commands, "_github_ssh_username", side_effect=ssh_side_effect
            )
        )
        if isinstance(remote_accessible, list):
            stack.enter_context(
                patch.object(
                    git_commands, "_git_remote_accessible",
                    side_effect=remote_accessible
                )
            )
        else:
            stack.enter_context(
                patch.object(
                    git_commands, "_git_remote_accessible",
                    return_value=remote_accessible
                )
            )
        stack.enter_context(
            patch.object(
                git_commands, "_git_config_global_get", side_effect=machine.get
            )
        )
        stack.enter_context(
            patch.object(
                git_commands, "_git_config_global_set", side_effect=machine.set
            )
        )
        stack.enter_context(
            patch.object(
                git_commands,
                "get_local_context_bindings",
                return_value={"user": "kamaji"}
            )
        )
        real_run = git_commands.subprocess.run

        def guarded_run(command, *arguments, **kwargs):
            if tuple(command) == ("git", "push", "-u", "origin", "main"):
                return subprocess.CompletedProcess(command, push_return, "", "")
            return real_run(command, *arguments, **kwargs)

        stack.enter_context(
            patch.object(git_commands.subprocess, "run", side_effect=guarded_run)
        )
        stack.enter_context(patch.dict(
            git_commands.os.environ, self.git_environment
        ))
        return stack.enter_context(patch.object(git_commands, "rot_say"))

    def test_alias_host_is_used_in_remote_url(self):
        self.write_accounts()
        machine = MachineConfigFake()
        hosts_tested = []

        with ExitStack() as stack:
            rot_say = self.enter_alias_patches(
                stack, machine, "github-rotbot", hosts_tested, True, 0
            )
            stack.enter_context(
                patch("builtins.input", side_effect=input_side_effect(("", "")))
            )
            result = git_commands.git_start(
                argparse.Namespace(), working_directory=self.project_directory
            )
        messages = [item.args[0] for item in rot_say.call_args_list]

        self.assertEqual(result, 0)
        self.assertEqual(hosts_tested, ["github-rotbot"])
        self.assertNotIn("github.com", hosts_tested)
        origin = self.git("remote", "get-url", "origin")
        self.assertEqual(
            origin.stdout.strip(),
            "git@github-rotbot:0xkamaji/example-project.git"
        )
        self.assertIn("SSH host:\n  github-rotbot", "\n".join(messages))

    def test_alias_is_used_for_ls_remote(self):
        self.write_accounts()
        machine = MachineConfigFake()
        hosts_tested = []
        probed_urls = []

        def probe(remote_url):
            probed_urls.append(remote_url)
            return True

        with ExitStack() as stack:
            rot_say = self.enter_alias_patches(
                stack, machine, "github-rotbot", hosts_tested, remote_accessible=True, push_return=0
            )
            stack.enter_context(
                patch.object(
                    git_commands, "_git_remote_accessible", side_effect=probe
                )
            )
            stack.enter_context(
                patch("builtins.input", side_effect=input_side_effect(("", "")))
            )
            result = git_commands.git_start(
                argparse.Namespace(), working_directory=self.project_directory
            )

        self.assertEqual(result, 0)
        self.assertNotEqual(probed_urls, [])
        self.assertTrue(
            all(url.startswith("git@github-rotbot:") for url in probed_urls)
        )
        self.assertTrue(
            all("github.com" not in url for url in probed_urls)
        )

    def test_code_does_not_switch_back_to_github_com(self):
        self.write_accounts()
        machine = MachineConfigFake()
        hosts_tested = []

        with ExitStack() as stack:
            rot_say = self.enter_alias_patches(
                stack, machine, "github-rotbot", hosts_tested, True, 0
            )
            stack.enter_context(
                patch("builtins.input", side_effect=input_side_effect(("", "")))
            )
            result = git_commands.git_start(
                argparse.Namespace(), working_directory=self.project_directory
            )
        messages = [item.args[0] for item in rot_say.call_args_list]

        self.assertEqual(result, 0)
        self.assertEqual(hosts_tested, ["github-rotbot"])
        self.assertNotIn("github.com", hosts_tested)

    def test_ssh_and_identity_checks_occur_before_git_init(self):
        self.write_accounts()
        machine = MachineConfigFake()
        hosts_tested = []
        init_ran = []

        def track_run(command, *arguments, **kwargs):
            if tuple(command) == ("git", "init", "-b", "main"):
                init_ran.append(True)
            return real_run(command, *arguments, **kwargs)

        real_run = git_commands.subprocess.run

        def ssh_side_effect(host="github.com"):
            hosts_tested.append(host)
            return None

        with ExitStack() as stack:
            stack.enter_context(
                patch.object(git_commands, "_machine_ssh_host", return_value="bad-host")
            )
            stack.enter_context(
                patch.object(
                    git_commands, "_github_ssh_username", side_effect=ssh_side_effect
                )
            )
            stack.enter_context(
                patch.object(
                    git_commands, "_git_config_global_get", side_effect=machine.get
                )
            )
            stack.enter_context(
                patch.object(
                    git_commands, "_git_config_global_set", side_effect=machine.set
                )
            )
            stack.enter_context(
                patch.object(
                    git_commands,
                    "get_local_context_bindings",
                    return_value={"user": "kamaji"}
                )
            )
            stack.enter_context(
                patch.object(git_commands.subprocess, "run", side_effect=track_run)
            )
            stack.enter_context(patch.dict(
                git_commands.os.environ, self.git_environment
            ))
            rot_say = stack.enter_context(patch.object(git_commands, "rot_say"))
            stack.enter_context(
                patch("builtins.input", side_effect=input_side_effect(("", "")))
            )
            result = git_commands.git_start(
                argparse.Namespace(), working_directory=self.project_directory
            )
        messages = [item.args[0] for item in rot_say.call_args_list]

        self.assertEqual(result, 1)
        self.assertEqual(init_ran, [])
        self.assertFalse((self.project_directory / ".git").exists())
        self.assertIn(
            "GitHub SSH authentication is not configured on this machine.",
            "\n".join(messages)
        )


def origin_string(instance):
    origin = instance.git("remote", "get-url", "origin")
    return origin.stdout.strip()


if __name__ == "__main__":
    unittest.main()