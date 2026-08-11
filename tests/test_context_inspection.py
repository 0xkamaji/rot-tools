import argparse
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from rotbot import __main__ as rotbot
from rotbot.cli import parser as command_parser
from rotbot.contexts import inspection, loader, machines, people


class ContextInspectionTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.context_root = self.root / "context"
        self.projects = self.context_root / "projects"
        self.people = self.context_root / "people"
        self.machines = self.context_root / "machines"
        self.projects.mkdir(parents=True)
        self.machines.mkdir()
        for role in people.PERSON_ROLES:
            (self.people / role).mkdir(parents=True)

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

        people.create_person_context("rot", "assistant", people_root=self.people)
        people.create_person_context("kamaji", "user", people_root=self.people)
        machines.create_machine("laptop", machines_root=self.machines)
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
        return directory

    def write_config(self, bindings="", defaults=True):
        self.config.parent.mkdir(parents=True, exist_ok=True)
        default_text = (
            "[defaults]\n"
            'assistant = "rot"\n'
            'user = "kamaji"\n'
            'machine = "laptop"\n'
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
            ("configured default", "configured default", "configured default")
        )
        self.assertEqual(result.warnings, ())

    def test_missing_configured_identity_is_unidentified(self):
        self.write_config()
        self.config.write_text(
            self.config.read_text(encoding="utf-8").replace(
                'assistant = "rot"',
                'assistant = "missing"'
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
            "# Match\n\n"
            "## Source\n\n"
            "Git remotes:\n"
            "- github.com/example/project\n\n"
            "Required paths:\n"
            "- README.md\n"
        )
        (self.projects / "project" / "match.md").write_text(
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

        with patch("rotbot.agents.runner.stream_agent") as stream_agent, patch(
            "rotbot.agents.runner.ask_agent"
        ) as ask_agent:
            inspection.inspect_current_context(self.outside)

        stream_agent.assert_not_called()
        ask_agent.assert_not_called()

    def test_handler_exit_codes_follow_contract(self):
        self.write_config()
        with patch.object(inspection, "rot_say") as rot_say:
            self.assertEqual(inspection.context_inspect(argparse.Namespace()), 0)
        self.assertIn("CURRENT ROTBOT CONTEXT", rot_say.call_args.args[0])

        self.config.write_text("[defaults]\nassistant = 7\n", encoding="utf-8")
        with patch.object(inspection, "rot_say") as rot_say:
            self.assertEqual(inspection.context_inspect(argparse.Namespace()), 2)
        self.assertIn("Invalid RotBot default assistant", rot_say.call_args.args[0])

        self.write_config()
        self.config.write_text(
            self.config.read_text(encoding="utf-8").replace(
                'user = "kamaji"',
                'user = "missing"'
            ),
            encoding="utf-8"
        )
        with patch.object(inspection, "rot_say"):
            self.assertEqual(inspection.context_inspect(argparse.Namespace()), 1)

    def test_malformed_configured_context_returns_setup_error(self):
        self.write_config()
        (self.people / "assistant" / "rot" / "metadata.toml").write_text(
            "not valid toml = [",
            encoding="utf-8"
        )

        with patch.object(inspection, "rot_say"):
            self.assertEqual(inspection.context_inspect(argparse.Namespace()), 2)

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


if __name__ == "__main__":
    unittest.main()
