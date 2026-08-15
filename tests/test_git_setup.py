import argparse
from contextlib import ExitStack
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from rotbot.commands import git as git_commands
from rotbot.contexts import accounts, entities, loader
from rotbot.contexts.config import ConfigError


USER_ID = "00000000-0000-4000-8000-000000000001"


class MachineConfigFake:
    def __init__(self):
        self.values = {"init.defaultBranch": "main"}
        self.fail_set = set()

    def get(self, key):
        return self.values.get(key, "")

    def set(self, key, value):
        if key in self.fail_set:
            return False
        if value == "":
            self.values.pop(key, None)
        else:
            self.values[key] = value
        return True


def input_side_effect(values):
    remaining = list(values)

    def side_effect(prompt=""):
        return remaining.pop(0) if remaining else ""

    return side_effect


class GitSetupTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.context_root = self.root / "context"
        self.user = entities.build_user_context("kamaji", context_id=USER_ID)
        entities.create_entity_context(self.user, root=self.context_root)
        self.user_directory = entities.entity_directory(
            self.user, root=self.context_root
        )
        self.loader_patch = patch.object(loader, "CONTEXT_ROOT", self.context_root)
        self.loader_patch.start()
        self.addCleanup(self.loader_patch.stop)
        self.addCleanup(self.temporary_directory.cleanup)

    def enter_patches(self, stack, machine, ssh_username, inputs):
        stack.enter_context(
            patch("builtins.input", side_effect=input_side_effect(inputs))
        )
        stack.enter_context(
            patch.object(
                git_commands, "_github_ssh_username", return_value=ssh_username
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
        return stack.enter_context(patch.object(git_commands, "rot_say"))

    def run_setup(self, machine, ssh_username, inputs=()):
        with ExitStack() as stack:
            rot_say = self.enter_patches(stack, machine, ssh_username, inputs)
            result = git_commands.git_setup(argparse.Namespace())
        messages = [item.args[0] for item in rot_say.call_args_list]
        return result, messages

    def write_accounts(self, account=None):
        account = account or accounts.AccountFile(
            git_name="Kamaji",
            git_email="kamaji@example.invalid",
            github_username="0xkamaji",
            github_default_visibility="private"
        )
        accounts.write_accounts(self.user_directory, account)
        return account


class GitSetupCommandTests(GitSetupTests):
    def test_setup_uses_current_user_binding_and_saves_identity(self):
        machine = MachineConfigFake()
        machine.values.update({
            "user.name": "Kamaji",
            "user.email": "kamaji@example.invalid"
        })

        result, messages = self.run_setup(machine, "0xkamaji")

        self.assertEqual(result, 0)
        path = self.user_directory / "accounts.toml"
        self.assertTrue(path.is_file())
        loaded = accounts.load_accounts(self.user_directory)
        self.assertEqual(loaded.git_name, "Kamaji")
        self.assertEqual(loaded.git_email, "kamaji@example.invalid")
        self.assertEqual(loaded.github_username, "0xkamaji")
        self.assertEqual(loaded.github_default_visibility, "private")

    def test_setup_seeds_from_machine_git_identity(self):
        machine = MachineConfigFake()
        machine.values.update({
            "user.name": "Existing Name",
            "user.email": "existing@example.invalid"
        })

        result, messages = self.run_setup(machine, "0xkamaji")

        self.assertEqual(result, 0)
        self.assertIn("Existing Git identity detected:", messages)
        loaded = accounts.load_accounts(self.user_directory)
        self.assertEqual(loaded.git_name, "Existing Name")
        self.assertEqual(loaded.git_email, "existing@example.invalid")

    def test_setup_prompts_for_git_author_name(self):
        machine = MachineConfigFake()

        result, messages = self.run_setup(
            machine, "0xkamaji",
            inputs=("Prompted Name", "prompt@example.invalid")
        )

        self.assertEqual(result, 0)
        self.assertIn("not the GitHub username", "\n".join(messages))
        loaded = accounts.load_accounts(self.user_directory)
        self.assertEqual(loaded.git_name, "Prompted Name")
        self.assertEqual(loaded.git_email, "prompt@example.invalid")

    def test_setup_keeps_existing_identity_without_prompts(self):
        self.write_accounts()
        machine = MachineConfigFake()
        machine.values.update({
            "user.name": "Different Name",
            "user.email": "different@example.invalid"
        })

        result, messages = self.run_setup(machine, "0xkamaji")

        self.assertEqual(result, 0)
        self.assertEqual(messages.count("Git author name"), 0)
        loaded = accounts.load_accounts(self.user_directory)
        self.assertEqual(loaded.git_name, "Kamaji")

    def test_setup_saves_accounts_to_user_context_root_only(self):
        machine = MachineConfigFake()
        machine.values.update({
            "user.name": "Kamaji",
            "user.email": "kamaji@example.invalid"
        })

        self.run_setup(machine, "0xkamaji")

        self.assertTrue((self.user_directory / "accounts.toml").is_file())
        self.assertFalse(
            (self.user_directory / "general" / "accounts.toml").exists()
        )
        self.assertFalse(
            (self.user_directory / "private" / "accounts.toml").exists()
        )

    def test_setup_rejects_invalid_visibility(self):
        self.write_accounts(
            accounts.AccountFile(
                git_name="Kamaji",
                git_email="kamaji@example.invalid",
                github_username="0xkamaji",
                github_default_visibility=""
            )
        )
        machine = MachineConfigFake()
        machine.values.update({
            "user.name": "Kamaji",
            "user.email": "kamaji@example.invalid"
        })

        result, messages = self.run_setup(
            machine, "0xkamaji", inputs=("secret",)
        )

        self.assertEqual(result, 1)
        self.assertIn("Invalid visibility: secret", messages)

    def test_setup_prompts_for_username_when_machine_ssh_missing(self):
        machine = MachineConfigFake()
        machine.values.update({
            "user.name": "Kamaji",
            "user.email": "kamaji@example.invalid"
        })

        result, messages = self.run_setup(
            machine, None, inputs=("y", "", "gh-user")
        )

        self.assertEqual(result, 0)
        self.assertIn(
            "GitHub SSH authentication could not be verified.",
            "\n".join(messages)
        )
        loaded = accounts.load_accounts(self.user_directory)
        self.assertEqual(loaded.github_username, "gh-user")

    def test_github_username_mismatch_prompts_for_replacement(self):
        self.write_accounts()
        machine = MachineConfigFake()
        machine.values.update({
            "user.name": "Kamaji",
            "user.email": "kamaji@example.invalid"
        })

        result, messages = self.run_setup(machine, "0xother", inputs=("y",))

        self.assertEqual(result, 0)
        self.assertIn("GitHub account mismatch.", messages)
        loaded = accounts.load_accounts(self.user_directory)
        self.assertEqual(loaded.github_username, "0xother")

    def test_setup_keeps_stored_username_when_replacement_declined(self):
        self.write_accounts()
        machine = MachineConfigFake()
        machine.values.update({
            "user.name": "Kamaji",
            "user.email": "kamaji@example.invalid"
        })

        result, messages = self.run_setup(machine, "0xother", inputs=("n",))

        self.assertEqual(result, 0)
        loaded = accounts.load_accounts(self.user_directory)
        self.assertEqual(loaded.github_username, "0xkamaji")

    def test_setup_can_be_cancelled_before_saving(self):
        self.write_accounts()
        machine = MachineConfigFake()
        machine.values.update({
            "user.name": "Kamaji",
            "user.email": "kamaji@example.invalid"
        })

        result, messages = self.run_setup(machine, "0xkamaji", inputs=("n", "n"))

        self.assertEqual(result, 1)
        self.assertIn("Git setup cancelled. No changes were made.", messages)
        loaded = accounts.load_accounts(self.user_directory)
        self.assertEqual(loaded.git_name, "Kamaji")

    def test_setup_keeps_machine_identity_when_replacement_declined(self):
        self.write_accounts()
        machine = MachineConfigFake()
        machine.values.update({
            "user.name": "Other Machine Name",
            "user.email": "machine@example.invalid"
        })

        result, messages = self.run_setup(machine, "0xkamaji")

        self.assertEqual(result, 0)
        self.assertIn("Machine Git identity was not changed.", messages)
        loaded = accounts.load_accounts(self.user_directory)
        self.assertEqual(loaded.git_name, "Kamaji")

    def test_setup_is_idempotent(self):
        self.write_accounts()
        machine = MachineConfigFake()
        machine.values.update({
            "user.name": "Kamaji",
            "user.email": "kamaji@example.invalid"
        })

        self.run_setup(machine, "0xkamaji")
        result, messages = self.run_setup(machine, "0xkamaji")

        self.assertEqual(result, 0)
        loaded = accounts.load_accounts(self.user_directory)
        self.assertEqual(loaded.git_name, "Kamaji")
        self.assertEqual(loaded.git_email, "kamaji@example.invalid")

    def test_setup_without_user_binding_returns_guidance(self):
        with patch.object(
            git_commands,
            "get_local_context_bindings",
            return_value={}
        ), patch.object(git_commands, "rot_say") as rot_say:
            result = git_commands.git_setup(argparse.Namespace())

        self.assertEqual(result, 1)
        messages = [item.args[0] for item in rot_say.call_args_list]
        self.assertEqual(messages[0], (
            "No Rot user is configured.\n"
            "Configure a user context before running Git setup."
        ))

    def test_setup_config_error_is_reported(self):
        with patch.object(
            git_commands,
            "get_local_context_bindings",
            side_effect=ConfigError("corrupt config")
        ), patch.object(git_commands, "rot_say") as rot_say:
            result = git_commands.git_setup(argparse.Namespace())

        self.assertEqual(result, 1)
        self.assertEqual(rot_say.call_args_list[0].args[0], "corrupt config")

    def test_setup_does_not_invoke_ai_functionality(self):
        machine = MachineConfigFake()
        machine.values.update({
            "user.name": "Kamaji",
            "user.email": "kamaji@example.invalid"
        })
        from rotbot.agents import invocation
        with ExitStack() as stack:
            self.enter_patches(stack, machine, "0xkamaji", ())
            invoke = stack.enter_context(patch.object(invocation, "invoke"))
            execute = stack.enter_context(patch.object(invocation, "execute"))
            result = git_commands.git_setup(argparse.Namespace())

        self.assertEqual(result, 0)
        invoke.assert_not_called()
        execute.assert_not_called()

    def test_setup_does_not_run_full_context_inspection(self):
        from rotbot.contexts import inspection
        person = Mock(name="kamaji")
        with ExitStack() as stack:
            machine = MachineConfigFake()
            rot_say = self.enter_patches(stack, machine, "0xkamaji", ())
            stack.enter_context(
                patch.object(
                    git_commands,
                    "_resolve_current_user",
                    return_value=(person, self.user_directory)
                )
            )
            stack.enter_context(
                patch.object(
                    git_commands, "_gather_git_identity",
                    return_value=("Kamaji", "kamaji@example.invalid")
                )
            )
            stack.enter_context(
                patch.object(
                    git_commands, "_github_identity",
                    return_value=("0xkamaji", True, "github.com", None)
                )
            )
            stack.enter_context(patch.object(accounts, "write_accounts"))
            inspect_existing = stack.enter_context(
                patch.object(entities, "load_user_documents")
            )
            inspect_current = stack.enter_context(
                patch.object(inspection, "inspect_current_context")
            )
            result = git_commands.git_setup(argparse.Namespace())

        self.assertEqual(result, 0)
        inspect_existing.assert_not_called()
        inspect_current.assert_not_called()


class GitIdentityHelperTests(unittest.TestCase):
    def test_ssh_greeting_parses_username_even_with_exit_code_one(self):
        for returncode in (0, 1):
            with self.subTest(returncode=returncode):
                result = Mock(
                    returncode=returncode,
                    stdout="",
                    stderr=(
                        "Hi 0xkamaji! You've successfully authenticated, "
                        "but GitHub does not provide shell access.\n"
                    )
                )
                with patch.object(
                    git_commands.subprocess, "run", return_value=result
                ):
                    self.assertEqual(
                        git_commands._github_ssh_username(), "0xkamaji"
                    )

    def test_permission_denied_returns_none(self):
        result = Mock(
            returncode=1,
            stdout="",
            stderr="git@github.com: Permission denied (publickey)."
        )
        with patch.object(git_commands.subprocess, "run", return_value=result):
            self.assertIsNone(git_commands._github_ssh_username())

    def test_ssh_timeout_returns_none(self):
        with patch.object(
            git_commands.subprocess,
            "run",
            side_effect=TimeoutError
        ):
            self.assertIsNone(git_commands._github_ssh_username())

    def test_ssh_missing_binary_returns_none(self):
        with patch.object(
            git_commands.subprocess, "run", side_effect=FileNotFoundError
        ):
            self.assertIsNone(git_commands._github_ssh_username())

    def test_github_ssh_command_is_bounded(self):
        result = Mock(returncode=1, stdout="", stderr="")
        with patch.object(git_commands.subprocess, "run", return_value=result) as run:
            git_commands._github_ssh_username()

        args = run.call_args.args[0]
        self.assertEqual(args[0:3], ["ssh", "-T", "-o"])
        self.assertIn("BatchMode=yes", args)
        self.assertIn("ConnectTimeout=10", args)
        self.assertIn("StrictHostKeyChecking=accept-new", args)
        self.assertIn("git@github.com", args)
        self.assertEqual(run.call_args.kwargs.get("timeout"), 20)

    def test_remote_accessible_uses_ls_remote_with_ssh_environment(self):
        result = Mock(returncode=0, stdout="", stderr="")
        with patch.object(
            git_commands.subprocess, "run", return_value=result
        ) as run, patch.object(
            git_commands, "_ssh_push_environment",
            return_value={"GIT_SSH_COMMAND": "ssh -o ConnectTimeout=10"}
        ):
            self.assertTrue(
                git_commands._git_remote_accessible(
                    "git@github.com:user/repo.git"
                )
            )
        args = run.call_args.args[0]
        self.assertEqual(args[:2], ["git", "ls-remote"])
        self.assertEqual(
            run.call_args.kwargs["env"],
            {"GIT_SSH_COMMAND": "ssh -o ConnectTimeout=10"}
        )
        self.assertEqual(run.call_args.kwargs.get("timeout"), 30)

    def test_remote_accessible_failure_returns_false(self):
        with patch.object(
            git_commands.subprocess,
            "run",
            return_value=Mock(returncode=1, stdout="", stderr="")
        ):
            self.assertFalse(
                git_commands._git_remote_accessible("git@github.com:user/repo.git")
            )

    def test_remote_accessible_timeout_returns_false(self):
        with patch.object(
            git_commands.subprocess, "run", side_effect=TimeoutError
        ):
            self.assertFalse(
                git_commands._git_remote_accessible("git@github.com:user/repo.git")
            )


class GitSetupPromptTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.context_root = self.root / "context"
        self.user = entities.build_user_context("kamaji", context_id=USER_ID)
        entities.create_entity_context(self.user, root=self.context_root)
        self.user_directory = entities.entity_directory(
            self.user, root=self.context_root
        )
        self.loader_patch = patch.object(loader, "CONTEXT_ROOT", self.context_root)
        self.loader_patch.start()
        self.addCleanup(self.loader_patch.stop)
        self.addCleanup(self.temporary_directory.cleanup)

    def run_setup_collecting_prompts(self, ssh_results, inputs, machine=None):
        prompts = []

        def ssh_side_effect(host="github.com"):
            return ssh_results.get(host)

        def input_side(prompt=" "):
            prompts.append(prompt)
            return (inputs.pop(0) if inputs else "")

        def machine_get(key):
            return machine.values.get(key, "") if machine else ""

        def machine_set(key, value):
            if machine:
                machine.values[key] = value
            return True

        with ExitStack() as stack:
            stack.enter_context(patch("builtins.input", side_effect=input_side))
            stack.enter_context(
                patch.object(
                    git_commands, "_github_ssh_username", side_effect=ssh_side_effect
                )
            )
            stack.enter_context(
                patch.object(git_commands, "_git_config_global_get", side_effect=machine_get)
            )
            stack.enter_context(
                patch.object(git_commands, "_git_config_global_set", side_effect=machine_set)
            )
            stack.enter_context(
                patch.object(
                    git_commands,
                    "get_local_context_bindings",
                    return_value={"user": "kamaji"}
                )
            )
            rot_say = stack.enter_context(patch.object(git_commands, "rot_say"))
            result = git_commands.git_setup(argparse.Namespace())
        messages = [item.args[0] for item in rot_say.call_args_list]
        return result, prompts, messages

    def test_github_username_prompt_contains_colon(self):
        machine = MachineConfigFake()
        machine.values.update({
            "user.name": "Kamaji",
            "user.email": "kamaji@example.invalid"
        })

        result, prompts, messages = self.run_setup_collecting_prompts(
            {"github.com": None}, ["", "", "0xkamaji", "", ""], machine
        )

        self.assertEqual(result, 0)
        self.assertTrue(
            any("GitHub username:" in prompt for prompt in prompts)
        )
        self.assertNotIn("GitHub username (unverified)", prompts)

    def test_manually_entered_username_is_not_labeled_verified(self):
        machine = MachineConfigFake()
        machine.values.update({
            "user.name": "Kamaji",
            "user.email": "kamaji@example.invalid"
        })

        result, prompts, messages = self.run_setup_collecting_prompts(
            {"github.com": None}, ["", "", "gh-user", "", ""], machine
        )

        self.assertEqual(result, 0)
        self.assertIn("GitHub SSH authentication could not be verified.", messages)
        self.assertIn("GitHub account saved.", messages)
        self.assertIn("SSH authentication is not verified on this machine.", messages)
        self.assertNotIn("SSH identity", prompts)
        loaded = accounts.load_accounts(self.user_directory)
        self.assertEqual(loaded.github_username, "gh-user")

    def test_verified_state_is_displayed_accurately(self):
        machine = MachineConfigFake()
        machine.values.update({
            "user.name": "Kamaji",
            "user.email": "kamaji@example.invalid"
        })

        result, prompts, messages = self.run_setup_collecting_prompts(
            {"github.com": "0xkamaji"}, [], machine
        )

        self.assertEqual(result, 0)
        joined = "\n".join(messages)
        self.assertIn("GitHub account:\n  0xkamaji", joined)
        self.assertIn("SSH authentication:\n  ✓ verified", joined)
        self.assertIn("✓ GitHub SSH authentication verified as 0xkamaji", joined)
        self.assertNotIn("SSH identity", prompts)

    def test_unverified_state_is_displayed_accurately(self):
        machine = MachineConfigFake()
        machine.values.update({
            "user.name": "Kamaji",
            "user.email": "kamaji@example.invalid"
        })

        result, prompts, messages = self.run_setup_collecting_prompts(
            {"github.com": None}, ["", "", "0xkamaji", "", ""], machine
        )

        self.assertEqual(result, 0)
        joined = "\n".join(messages)
        self.assertIn("SSH authentication:\n  not verified on this machine", joined)


class GitSetupAliasTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.context_root = self.root / "context"
        self.user = entities.build_user_context("kamaji", context_id=USER_ID)
        entities.create_entity_context(self.user, root=self.context_root)
        self.user_directory = entities.entity_directory(
            self.user, root=self.context_root
        )
        self.loader_patch = patch.object(loader, "CONTEXT_ROOT", self.context_root)
        self.loader_patch.start()
        self.addCleanup(self.loader_patch.stop)
        self.addCleanup(self.temporary_directory.cleanup)
        accounts.write_accounts(
            self.user_directory,
            accounts.AccountFile(
                git_name="Kamaji",
                git_email="kamaji@example.invalid",
                github_username="0xkamaji",
                github_default_visibility="private"
            )
        )

    def run_setup(self, ssh_callables, inputs, machine):
        hosts_tested = []

        def ssh_side_effect(host="github.com"):
            hosts_tested.append(host)
            return ssh_callables.get(host)

        def machine_get(key):
            return machine.values.get(key, "")

        def machine_set(key, value):
            if value == "":
                machine.values.pop(key, None)
            else:
                machine.values[key] = value
            return True

        with ExitStack() as stack:
            stack.enter_context(
                patch("builtins.input", side_effect=input_side_effect(inputs))
            )
            stack.enter_context(
                patch.object(
                    git_commands, "_github_ssh_username", side_effect=ssh_side_effect
                )
            )
            stack.enter_context(
                patch.object(git_commands, "_git_config_global_get", side_effect=machine_get)
            )
            stack.enter_context(
                patch.object(git_commands, "_git_config_global_set", side_effect=machine_set)
            )
            stack.enter_context(
                patch.object(
                    git_commands,
                    "get_local_context_bindings",
                    return_value={"user": "kamaji"}
                )
            )
            rot_say = stack.enter_context(patch.object(git_commands, "rot_say"))
            result = git_commands.git_setup(argparse.Namespace())
        messages = [item.args[0] for item in rot_say.call_args_list]
        return result, messages, hosts_tested

    def test_github_com_failure_falls_back_to_user_alias(self):
        machine = MachineConfigFake()
        machine.values.update({
            "user.name": "Kamaji",
            "user.email": "kamaji@example.invalid"
        })

        result, messages, hosts_tested = self.run_setup(
            {"github.com": None, "github-rotbot": "0xkamaji"},
            ["github-rotbot"],
            machine
        )

        self.assertEqual(result, 0)
        self.assertIn("github.com", hosts_tested)
        self.assertIn("github-rotbot", hosts_tested)
        joined = "\n".join(messages)
        self.assertIn("GitHub SSH authentication via github.com failed.", joined)
        self.assertIn("✓ GitHub SSH authentication verified as 0xkamaji", joined)
        loaded = accounts.load_accounts(self.user_directory)
        self.assertEqual(loaded.github_username, "0xkamaji")

    def test_failed_alias_leaves_username_stored_unverified(self):
        machine = MachineConfigFake()
        machine.values.update({
            "user.name": "Kamaji",
            "user.email": "kamaji@example.invalid"
        })

        result, messages, hosts_tested = self.run_setup(
            {"github.com": None, "github-rotbot": None},
            ["github-rotbot"],
            machine
        )

        self.assertEqual(result, 0)
        joined = "\n".join(messages)
        self.assertIn("GitHub SSH authentication could not be verified.", joined)
        self.assertIn("SSH authentication is not verified on this machine.", joined)


class GitSetupMachineConfigTests(GitSetupTests):
    def run_setup_prompts(self, machine, ssh_username, inputs):
        prompts = []

        def input_side(prompt=" "):
            prompts.append(prompt)
            return (list(inputs).pop(0) if inputs else "")

        def ssh_side_effect(host="github.com"):
            return ssh_username

        with ExitStack() as stack:
            stack.enter_context(patch("builtins.input", side_effect=input_side))
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
            rot_say = stack.enter_context(patch.object(git_commands, "rot_say"))
            result = git_commands.git_setup(argparse.Namespace())
        messages = [item.args[0] for item in rot_say.call_args_list]
        return result, prompts, messages

    def test_machine_ssh_host_is_not_written_to_accounts_toml(self):
        machine = MachineConfigFake()
        machine.values.update({
            "user.name": "Kamaji",
            "user.email": "kamaji@example.invalid"
        })

        result, messages = self.run_setup(machine, "0xkamaji")

        self.assertEqual(result, 0)
        content = (self.user_directory / "accounts.toml").read_text(encoding="utf-8")
        self.assertNotIn("ssh_host", content)
        self.assertNotIn("github-rotbot", content)

    def test_failed_git_config_write_produces_failure(self):
        self.write_accounts()
        machine = MachineConfigFake()
        machine.values.update({
            "user.name": "Different Machine",
            "user.email": "different@example.invalid"
        })
        machine.fail_set = {"user.name", "user.email"}

        result, prompts, messages = self.run_setup_prompts(
            machine, "0xkamaji", ["y"]
        )

        self.assertEqual(result, 1)
        self.assertNotIn("✓ configured Git on this machine", messages)
        self.assertIn("Could not configure Git on this machine.", messages)

    def test_success_message_only_after_successful_writes(self):
        machine = MachineConfigFake()
        machine.values.update({
            "user.name": "Kamaji",
            "user.email": "kamaji@example.invalid"
        })

        result, messages = self.run_setup(machine, "0xkamaji")

        self.assertEqual(result, 0)
        self.assertIn("✓ configured Git on this machine", messages)

    def test_unset_machine_identity_uses_configure_semantics_default_yes(self):
        self.write_accounts()
        machine = MachineConfigFake()

        result, prompts, messages = self.run_setup_prompts(machine, "0xkamaji", [])

        self.assertEqual(result, 0)
        joined = "\n".join(messages)
        self.assertTrue(
            any("Configure this machine with this Git identity?" in prompt for prompt in prompts)
        )
        self.assertIn("✓ configured Git on this machine", joined)
        self.assertEqual(machine.values["user.name"], "Kamaji")

    def test_conflicting_identity_uses_replace_semantics_default_no(self):
        self.write_accounts()
        machine = MachineConfigFake()
        machine.values.update({
            "user.name": "Other Machine Name",
            "user.email": "machine@example.invalid"
        })

        result, prompts, messages = self.run_setup_prompts(machine, "0xkamaji", [])

        self.assertEqual(result, 0)
        joined = "\n".join(messages)
        self.assertTrue(
            any("Replace this machine's Git identity?" in prompt for prompt in prompts)
        )
        self.assertIn("Machine Git identity was not changed.", joined)
        self.assertNotIn("✓ configured Git on this machine", joined)


if __name__ == "__main__":
    unittest.main()