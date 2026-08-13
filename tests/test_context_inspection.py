import argparse
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import tomllib
import unittest
from unittest.mock import patch

from rotbot import __main__ as rotbot
from rotbot.cli import parser as command_parser
from rotbot.contexts import entities, inspection, loader, machines
from rotbot.contexts.config import get_local_context_bindings
from rotbot.commands.machine import MachineInspection


class ContextInspectionTests(unittest.TestCase):
    USER_ID = "00000000-0000-4000-8000-000000000001"
    ASSISTANT_ID = "00000000-0000-4000-8000-000000000002"
    MACHINE_ID = "00000000-0000-4000-8000-000000000003"

    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.context_root = self.root / "context"
        self.projects = self.context_root / "projects"
        self.users = self.context_root / "users"
        self.assistants = self.context_root / "assistants"
        self.machines = self.context_root / "machines"
        self.projects.mkdir(parents=True)
        self.machines.mkdir()

        self.config_home = self.root / "config-home"
        self.config = self.config_home / "rotbot" / "config.toml"
        self.environment = patch.dict(
            os.environ,
            {"XDG_CONFIG_HOME": str(self.config_home)},
            clear=True
        )
        self.context_patch = patch.object(loader, "CONTEXT_ROOT", self.context_root)
        self.environment.start()
        self.context_patch.start()

        entities.create_entity_context(
            entities.build_assistant_context(
                "rot", context_id=self.ASSISTANT_ID
            ),
            root=self.context_root
        )
        entities.create_entity_context(
            entities.build_user_context(
                "kamaji", context_id=self.USER_ID
            ),
            root=self.context_root
        )
        machines.create_machine(
            "laptop", context_id=self.MACHINE_ID, machines_root=self.machines
        )
        self.create_project("project")
        self.outside = self.root / "outside"
        self.outside.mkdir()

    def tearDown(self):
        self.context_patch.stop()
        self.environment.stop()
        self.temporary_directory.cleanup()

    def create_project(self, name):
        directory = self.projects / name
        directory.mkdir()
        (directory / "identity.md").write_text(f"{name} identity\n", encoding="utf-8")
        (directory / "state.md").write_text(f"{name} state\n", encoding="utf-8")
        (directory / "metadata.toml").write_text(
            loader.render_project_metadata(name),
            encoding="utf-8"
        )
        return directory

    def write_config(self, bindings="", defaults=True):
        self.config.parent.mkdir(parents=True, exist_ok=True)
        default_text = (
            "[user]\n"
            f'id = "{self.USER_ID}"\n\n'
            "[assistant]\n"
            f'id = "{self.ASSISTANT_ID}"\n\n'
            "[machine]\n"
            f'id = "{self.MACHINE_ID}"\n'
        ) if defaults else ""
        separator = "\n" if default_text and bindings else ""
        self.config.write_text(default_text + separator + bindings, encoding="utf-8")

    def source_binding(self, name, path):
        return f'[contexts.{name}]\nsource_path = "{path}"\n'

    def production_binding(self, name, path):
        return f'[contexts.{name}]\nproduction_path = "{path}"\n'

    def test_defaults_identify_assistant_user_and_machine(self):
        self.write_config()

        result = inspection.inspect_current_context(self.outside)

        self.assertEqual(result.assistant, "rot")
        self.assertEqual(result.user, "kamaji")
        self.assertEqual(result.machine, "laptop")
        self.assertEqual(
            result.identification_sources[:3],
            ("local config", "local config", "local config")
        )
        self.assertEqual(result.warnings, ())

    def test_missing_configured_identity_is_unidentified(self):
        self.write_config()
        self.config.write_text(
            self.config.read_text(encoding="utf-8").replace(
                f'id = "{self.ASSISTANT_ID}"',
                'id = "missing"',
                1
            ),
            encoding="utf-8"
        )

        result = inspection.inspect_current_context(self.outside)
        output = inspection.render_inspected_context(result)

        self.assertIsNone(result.assistant)
        self.assertIn("Assistant:  unidentified", output)
        self.assertTrue(any("missing" in warning for warning in result.warnings))

    def test_source_binding_matches_project_root(self):
        source = self.root / "source"
        source.mkdir()
        self.write_config(self.source_binding("project", source))

        result = inspection.inspect_current_context(source)

        self.assertEqual(result.project, "project")
        self.assertEqual(result.identification_sources.project, "source binding")

    def test_source_binding_matches_nested_directory(self):
        source = self.root / "source"
        nested = source / "src" / "package"
        nested.mkdir(parents=True)
        self.write_config(self.source_binding("project", source))

        result = inspection.inspect_current_context(nested)

        self.assertEqual(result.project, "project")
        self.assertEqual(result.cwd, nested.resolve())

    def test_production_binding_identifies_project(self):
        production = self.root / "production"
        nested = production / "assets"
        nested.mkdir(parents=True)
        self.write_config(self.production_binding("project", production))

        result = inspection.inspect_current_context(nested)

        self.assertEqual(result.project, "project")
        self.assertEqual(result.identification_sources.project, "production binding")

    def test_source_binding_precedes_more_specific_production_binding(self):
        self.create_project("production-project")
        source = self.root / "workspace"
        production = source / "deployed"
        production.mkdir(parents=True)
        self.write_config(
            self.source_binding("project", source)
            + "\n"
            + self.production_binding("production-project", production)
        )

        result = inspection.inspect_current_context(production)

        self.assertEqual(result.project, "project")
        self.assertEqual(result.identification_sources.project, "source binding")

    def test_outside_known_projects_reports_none_without_failure(self):
        self.write_config()

        result = inspection.inspect_current_context(self.outside)
        output = inspection.render_inspected_context(result)

        self.assertIsNone(result.project)
        self.assertEqual(
            result.identification_sources.project,
            "no matching project context"
        )
        self.assertIn("Project:    none", output)
        self.assertEqual(result.warnings, ())

    def test_safe_repository_matching_is_used_after_bindings(self):
        match_document = (
            "[source]\n"
            "is_git_repo = true\n"
            'git_remotes = ["github.com/example/project"]\n'
            'required_paths = ["README.md"]\n'
        )
        (self.projects / "project" / "match.toml").write_text(
            match_document,
            encoding="utf-8"
        )
        repository = self.root / "repository"
        nested = repository / "src"
        nested.mkdir(parents=True)
        subprocess.run(["git", "init", "-q"], cwd=repository, check=True)
        subprocess.run(
            [
                "git", "remote", "add", "origin",
                "git@github.com:example/project.git"
            ],
            cwd=repository,
            check=True
        )
        (repository / "README.md").write_text("project\n", encoding="utf-8")
        self.write_config()

        result = inspection.inspect_current_context(nested)

        self.assertEqual(result.project, "project")
        self.assertEqual(result.identification_sources.project, "project match")

    def test_non_git_project_matching_is_used_from_nested_directory(self):
        (self.projects / "project" / "match.toml").write_text(
            "[source]\n"
            "is_git_repo = false\n"
            'required_paths = ["project.toml", "src/"]\n',
            encoding="utf-8"
        )
        project = self.root / "plain-project"
        nested = project / "src" / "nested"
        nested.mkdir(parents=True)
        (project / "project.toml").write_text("name = 'plain'\n", encoding="utf-8")
        self.write_config()

        result = inspection.inspect_current_context(nested)

        self.assertEqual(result.project, "project")
        self.assertEqual(result.identification_sources.project, "project match")

    def test_most_specific_nested_binding_wins(self):
        self.create_project("nested")
        outer = self.root / "workspace"
        inner = outer / "nested"
        current = inner / "src"
        current.mkdir(parents=True)
        self.write_config(
            self.source_binding("project", outer)
            + "\n"
            + self.source_binding("nested", inner)
        )

        result = inspection.inspect_current_context(current)

        self.assertEqual(result.project, "nested")

    def test_equally_specific_bindings_are_reported_as_ambiguous(self):
        self.create_project("other")
        source = self.root / "shared"
        source.mkdir()
        self.write_config(
            self.source_binding("project", source)
            + "\n"
            + self.source_binding("other", source)
        )

        result = inspection.inspect_current_context(source)
        output = inspection.render_inspected_context(result)

        self.assertIsNone(result.project)
        self.assertIn("ambiguous source binding", output)
        self.assertIn("other, project", output)
        self.assertTrue(result.warnings)

    def test_private_machine_metadata_is_never_rendered(self):
        self.write_config()
        machines.create_local_machine_record(
            "laptop",
            {
                "connection": {"hostname": "private-host"},
                "network": [{"address": "192.0.2.10"}]
            },
            self.MACHINE_ID,
            target_config=self.config
        )

        output = inspection.render_inspected_context(
            inspection.inspect_current_context(self.outside)
        )

        self.assertIn("Local/private machine metadata: excluded", output)
        self.assertNotIn("private-host", output)
        self.assertNotIn("192.0.2.10", output)

    def test_inspection_performs_no_writes(self):
        source = self.root / "source"
        source.mkdir()
        self.write_config(self.source_binding("project", source))

        def snapshot():
            return {
                path.relative_to(self.root): (path.stat().st_mtime_ns, path.read_bytes())
                for path in self.root.rglob("*")
                if path.is_file()
            }

        before = snapshot()
        inspection.inspect_current_context(source)

        self.assertEqual(snapshot(), before)

    def test_inspection_never_invokes_an_ai_agent(self):
        self.write_config()

        with patch("rotbot.agents.invocation.invoke") as stream_agent, patch(
            "rotbot.agents.runner.ask_agent"
        ) as ask_agent:
            inspection.inspect_current_context(self.outside)

        stream_agent.assert_not_called()
        ask_agent.assert_not_called()

    def test_missing_user_prompts_and_persists_selection(self):
        self.write_config()
        content = self.config.read_text(encoding="utf-8")
        start = content.index("[user]")
        end = content.index("[assistant]")
        self.config.write_text(content[:start] + content[end:], encoding="utf-8")

        with patch("builtins.input", return_value="1"), patch.object(
            inspection,
            "rot_say"
        ):
            result = inspection.inspect_current_context(self.outside, bootstrap=True)

        self.assertEqual(result.user, "kamaji")
        self.assertEqual(get_local_context_bindings()["user"], self.USER_ID)

    def test_missing_assistant_prompts_and_persists_selection(self):
        self.write_config()
        content = self.config.read_text(encoding="utf-8")
        start = content.index("[assistant]")
        end = content.index("[machine]")
        self.config.write_text(content[:start] + content[end:], encoding="utf-8")

        with patch("builtins.input", return_value="1"), patch.object(
            inspection,
            "rot_say"
        ):
            result = inspection.inspect_current_context(self.outside, bootstrap=True)

        self.assertEqual(result.assistant, "rot")
        self.assertEqual(
            get_local_context_bindings()["assistant"], self.ASSISTANT_ID
        )

    def test_no_existing_user_reuses_context_add_and_persists_created_user(self):
        shutil.rmtree(self.users / "kamaji")
        self.write_config()
        content = self.config.read_text(encoding="utf-8")
        start = content.index("[user]")
        end = content.index("[assistant]")
        self.config.write_text(content[:start] + content[end:], encoding="utf-8")

        def create_user(args):
            self.assertEqual(args.context_type, "user")
            entities.create_entity_context(
                entities.build_user_context("new-user"),
                root=self.context_root
            )
            return 0

        with patch("builtins.input", return_value="1"), patch(
            "rotbot.contexts.creation.context_add",
            side_effect=create_user
        ) as context_add, patch.object(inspection, "rot_say"):
            result = inspection.inspect_current_context(self.outside, bootstrap=True)

        context_add.assert_called_once()
        self.assertEqual(result.user, "new-user")
        self.assertEqual(get_local_context_bindings()["user"], result.user_id)

    def test_add_assistant_option_reuses_context_add(self):
        shutil.rmtree(self.assistants / "rot")
        self.write_config()
        content = self.config.read_text(encoding="utf-8")
        start = content.index("[assistant]")
        end = content.index("[machine]")
        self.config.write_text(content[:start] + content[end:], encoding="utf-8")

        def create_assistant(args):
            self.assertEqual(args.context_type, "assistant")
            entities.create_entity_context(
                entities.build_assistant_context("new-assistant"),
                root=self.context_root
            )
            return 0

        # The repository built-in Rot remains available when inspection uses
        # the default root, so adding a new assistant is the second option.
        with patch("builtins.input", return_value="2"), patch(
            "rotbot.contexts.creation.context_add",
            side_effect=create_assistant
        ) as context_add, patch.object(inspection, "rot_say"):
            result = inspection.inspect_current_context(self.outside, bootstrap=True)

        context_add.assert_called_once()
        self.assertEqual(result.assistant, "new-assistant")
        self.assertEqual(
            get_local_context_bindings()["assistant"],
            result.assistant_id
        )

    def test_missing_machine_inspects_registers_and_persists(self):
        self.write_config()
        content = self.config.read_text(encoding="utf-8")
        self.config.write_text(content[:content.index("[machine]")], encoding="utf-8")
        detected = MachineInspection(
            {"operating_system": "TestOS", "architecture": "x86_64"},
            {"connection": {"hostname": "desktop-host"}}
        )

        with patch(
            "rotbot.commands.machine.inspect_local_machine",
            return_value=detected
        ) as inspect_machine, patch(
            "rotbot.commands.machine.show_inspection"
        ), patch(
            "rotbot.commands.machine._ask_machine_display_name",
            return_value="Desktop Host"
        ), patch(
            "rotbot.commands.machine._confirm_registration",
            return_value=True
        ), patch("rotbot.commands.machine.rot_say"), patch.object(
            inspection,
            "rot_say"
        ):
            result = inspection.inspect_current_context(self.outside, bootstrap=True)

        inspect_machine.assert_called_once_with()
        self.assertEqual(result.machine, "desktop-host")
        self.assertEqual(get_local_context_bindings()["machine"], result.machine_id)
        self.assertEqual(machines.load_machine_context("desktop-host").name, "desktop-host")

    def test_machine_bootstrap_does_not_reuse_hostname_collision(self):
        machines.create_machine("desktop-host", machines_root=self.machines)
        marker = self.machines / "desktop-host" / "identity.md"
        marker.write_text("unrelated machine\n", encoding="utf-8")
        self.write_config()
        content = self.config.read_text(encoding="utf-8")
        self.config.write_text(content[:content.index("[machine]")], encoding="utf-8")
        detected = MachineInspection(
            {"operating_system": "TestOS"},
            {"connection": {"hostname": "desktop-host"}}
        )

        with patch(
            "rotbot.commands.machine.inspect_local_machine",
            return_value=detected
        ), patch("rotbot.commands.machine.show_inspection"), patch(
            "rotbot.commands.machine._ask_machine_display_name",
            return_value="Desktop Host 2"
        ), patch(
            "rotbot.commands.machine._confirm_registration",
            return_value=True
        ), patch(
            "rotbot.commands.machine.rot_say"
        ), patch.object(inspection, "rot_say"):
            result = inspection.inspect_current_context(self.outside, bootstrap=True)

        self.assertEqual(result.machine, "desktop-host-2")
        self.assertEqual(marker.read_text(encoding="utf-8"), "unrelated machine\n")

    def test_configured_machine_does_not_trigger_hardware_inspection(self):
        self.write_config()

        with patch("rotbot.commands.machine.inspect_local_machine") as inspect_machine:
            result = inspection.inspect_current_context(self.outside, bootstrap=True)

        inspect_machine.assert_not_called()
        self.assertEqual(result.machine, "laptop")

    def test_declined_machine_registration_remains_unidentified(self):
        self.write_config()
        content = self.config.read_text(encoding="utf-8")
        self.config.write_text(content[:content.index("[machine]")], encoding="utf-8")
        detected = MachineInspection(
            {}, {"connection": {"hostname": "desktop-host"}}
        )

        with patch(
            "rotbot.commands.machine.inspect_local_machine",
            return_value=detected
        ), patch("rotbot.commands.machine.show_inspection"), patch(
            "rotbot.commands.machine._ask_machine_display_name",
            return_value="Desktop Host"
        ), patch(
            "rotbot.commands.machine._confirm_registration",
            return_value=False
        ), patch("rotbot.commands.machine.rot_say"), patch.object(
            inspection,
            "rot_say"
        ):
            result = inspection.inspect_current_context(self.outside, bootstrap=True)

        self.assertIsNone(result.machine)
        self.assertTrue(any("No local machine" in warning for warning in result.warnings))
        self.assertNotIn("machine", get_local_context_bindings())

    def test_stale_machine_binding_runs_machine_bootstrap(self):
        self.write_config()
        self.config.write_text(
            self.config.read_text(encoding="utf-8").replace(
                f'id = "{self.MACHINE_ID}"',
                'id = "missing-machine"'
            ),
            encoding="utf-8"
        )
        detected = MachineInspection(
            {"operating_system": "TestOS"},
            {"connection": {"hostname": "replacement-host"}}
        )

        with patch(
            "rotbot.commands.machine.inspect_local_machine",
            return_value=detected
        ) as inspect_machine, patch(
            "rotbot.commands.machine.show_inspection"
        ), patch(
            "rotbot.commands.machine._ask_machine_display_name",
            return_value="Replacement Host"
        ), patch(
            "rotbot.commands.machine._confirm_registration",
            return_value=True
        ), patch("rotbot.commands.machine.rot_say"), patch.object(
            inspection,
            "rot_say"
        ):
            result = inspection.inspect_current_context(self.outside, bootstrap=True)

        inspect_machine.assert_called_once_with()
        self.assertEqual(result.machine, "replacement-host")
        self.assertEqual(
            get_local_context_bindings()["machine"],
            result.machine_id
        )

    def test_machine_inspection_failure_is_a_setup_error(self):
        self.write_config()
        content = self.config.read_text(encoding="utf-8")
        self.config.write_text(content[:content.index("[machine]")], encoding="utf-8")

        with patch(
            "rotbot.commands.machine.inspect_local_machine",
            side_effect=RuntimeError("inspection failed")
        ), patch(
            "rotbot.commands.machine.rot_say"
        ), patch.object(
            inspection,
            "rot_say"
        ), self.assertRaisesRegex(
            inspection.ContextInspectionError,
            "exit code 1"
        ):
            inspection.inspect_current_context(self.outside, bootstrap=True)

    def test_stale_user_binding_is_replaced(self):
        self.write_config()
        self.config.write_text(
            self.config.read_text(encoding="utf-8").replace(
                f'id = "{self.USER_ID}"',
                'id = "missing"',
                1
            ),
            encoding="utf-8"
        )

        with patch("builtins.input", return_value="1"), patch.object(
            inspection,
            "rot_say"
        ):
            result = inspection.inspect_current_context(self.outside, bootstrap=True)

        self.assertEqual(result.user, "kamaji")
        self.assertEqual(get_local_context_bindings()["user"], self.USER_ID)

    def test_project_changes_with_directory_without_changing_local_bindings(self):
        self.create_project("other")
        first = self.root / "first"
        second = self.root / "second"
        first.mkdir()
        second.mkdir()
        self.write_config(
            self.source_binding("project", first)
            + "\n"
            + self.source_binding("other", second)
        )
        before = get_local_context_bindings()

        first_result = inspection.inspect_current_context(first)
        second_result = inspection.inspect_current_context(second)

        self.assertEqual(first_result.project, "project")
        self.assertEqual(second_result.project, "other")
        self.assertEqual(get_local_context_bindings(), before)
        self.assertNotIn("[project]", self.config.read_text(encoding="utf-8"))

    def test_first_run_in_project_never_persists_project_as_default(self):
        source = self.root / "source"
        source.mkdir()
        self.write_config(self.source_binding("project", source), defaults=False)
        detected = MachineInspection(
            {"operating_system": "TestOS"},
            {"connection": {"hostname": "first-run-host"}}
        )

        with patch("builtins.input", side_effect=("1", "1")), patch(
            "rotbot.commands.machine.inspect_local_machine",
            return_value=detected
        ), patch("rotbot.commands.machine.show_inspection"), patch(
            "rotbot.commands.machine._ask_machine_display_name",
            return_value="First Run Host"
        ), patch(
            "rotbot.commands.machine._confirm_registration",
            return_value=True
        ), patch(
            "rotbot.commands.machine.rot_say"
        ), patch.object(inspection, "rot_say"):
            result = inspection.inspect_current_context(source, bootstrap=True)

        document = tomllib.loads(self.config.read_text(encoding="utf-8"))
        self.assertEqual(result.project, "project")
        self.assertEqual(set(document) & {"user", "assistant", "machine"}, {
            "user", "assistant", "machine"
        })
        self.assertNotIn("project", document)
        self.assertNotIn("defaults", document)

    def test_handler_exit_codes_follow_contract(self):
        self.write_config()
        with patch.object(inspection, "rot_say") as rot_say:
            self.assertEqual(inspection.context_inspect(argparse.Namespace()), 0)
        self.assertIn("CURRENT ROTBOT CONTEXT", rot_say.call_args.args[0])

        self.config.write_text("[assistant]\nid = 7\n", encoding="utf-8")
        with patch.object(inspection, "rot_say") as rot_say:
            self.assertEqual(inspection.context_inspect(argparse.Namespace()), 2)
        self.assertIn("Invalid local assistant context ID", rot_say.call_args.args[0])

        self.write_config()
        self.config.write_text(
            self.config.read_text(encoding="utf-8").replace(
                f'id = "{self.USER_ID}"',
                'id = "missing"',
                1
            ),
            encoding="utf-8"
        )
        with patch("builtins.input", return_value=""), patch.object(
            inspection,
            "rot_say"
        ):
            self.assertEqual(inspection.context_inspect(argparse.Namespace()), 1)

    def test_malformed_configured_context_is_treated_as_stale(self):
        self.write_config()
        (self.assistants / "rot" / "metadata.toml").write_text(
            "not valid toml = [",
            encoding="utf-8"
        )

        with patch("builtins.input", return_value=""), patch.object(
            inspection,
            "rot_say"
        ):
            self.assertEqual(inspection.context_inspect(argparse.Namespace()), 1)

    def test_exit_code_survives_parser_and_top_level_dispatch(self):
        self.write_config()
        arguments = command_parser.parse_args(["context", "inspect"])
        inspected = inspection.inspect_current_context(self.outside)._replace(
            warnings=("configured identity is invalid",)
        )

        with patch.object(
            inspection,
            "inspect_current_context",
            return_value=inspected
        ), patch.object(inspection, "rot_say"), patch.object(
            rotbot,
            "parse_args",
            return_value=arguments
        ):
            result = rotbot.main()

        self.assertEqual(result, 1)


class ContextInspectParserTests(unittest.TestCase):
    def assert_rejected(self, argv):
        with patch.object(command_parser, "rot_say"), self.assertRaises(SystemExit) as raised:
            command_parser.parse_args(argv)
        self.assertEqual(raised.exception.code, 2)

    def test_positional_directory_is_rejected(self):
        self.assert_rejected(["context", "inspect", "/some/path"])

    def test_context_resolve_command_does_not_exist(self):
        self.assert_rejected(["context", "resolve"])

    def test_context_add_accepts_user_and_assistant_roles(self):
        for context_type in ("user", "assistant"):
            with self.subTest(context_type=context_type):
                args = command_parser.parse_args(
                    ["context", "add", context_type, "example"]
                )
                self.assertEqual(args.context_type, context_type)
                self.assertEqual(args.name, "example")


if __name__ == "__main__":
    unittest.main()
