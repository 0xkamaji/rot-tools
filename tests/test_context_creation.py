import argparse
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import ANY, patch

from rotbot.commands.machine import MachineInspection
from rotbot.contexts import creation as context_creation
from rotbot.contexts import loader as contexts
from rotbot.contexts import matching as context_matching
from rotbot.contexts.config import ConfigError, config_path, get_context_binding


class ContextCreationTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.context_root = self.root / "contexts"
        self.context_root.mkdir()
        self.project_context_root = self.context_root / "projects"
        self.project_context_root.mkdir()
        self.project = self.root / "project"
        self.project.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=self.project, check=True)
        subprocess.run(
            [
                "git", "remote", "add", "upstream",
                "git@github.com:example/project.git"
            ],
            cwd=self.project,
            check=True
        )
        (self.project / "README.md").write_text("# Example\nSafe docs.\n", encoding="utf-8")
        (self.project / "main.py").write_text("def main():\n    return 0\n", encoding="utf-8")
        (self.project / "src").mkdir()

        self.context_patch = patch.object(contexts, "CONTEXT_ROOT", self.context_root)
        self.context_patch.start()
        self.environment = patch.dict(
            os.environ,
            {"XDG_CONFIG_HOME": str(self.root / "config-home")},
            clear=True
        )
        self.environment.start()
        self.identity_output = (
            "# example\n\n- Example is a small test project.\n"
            "- Its identity is human-maintained."
        )
        self.state_output = (
            "# example State\n\n- The project has a Python entry point.\n"
            "- A source directory is present."
        )

    def tearDown(self):
        self.environment.stop()
        self.context_patch.stop()
        self.temporary_directory.cleanup()

    def args(self, name="example", path=None):
        return argparse.Namespace(
            name=name,
            path=str(self.project if path is None else path)
        )

    def run_add(self, answer="n", args=None):
        args = self.args() if args is None else args
        with patch("builtins.input", return_value=answer), patch.object(
            context_creation,
            "rot_say"
        ) as rot_say, patch.object(context_creation, "rot_continue") as rot_continue:
            result = context_creation._add_project_context(args)
        return result, rot_say, rot_continue

    def test_invalid_names_and_existing_context_are_rejected_before_creation(self):
        for name in ("", "../outside", "nested/name", ".hidden"):
            with self.subTest(name=name):
                result, _rot_say, _rot_continue = self.run_add(
                    args=self.args(name=name)
                )
                self.assertEqual(result, 1)

        existing = self.project_context_root / "existing"
        existing.mkdir()
        (existing / "identity.md").write_text("unchanged", encoding="utf-8")
        result, rot_say, _rot_continue = self.run_add(
            args=self.args(name="existing")
        )
        self.assertEqual(result, 1)
        self.assertIn("already exists", rot_say.call_args.args[0])
        self.assertEqual(
            (existing / "identity.md").read_text(encoding="utf-8"),
            "unchanged"
        )

    def test_invalid_project_paths_are_rejected_before_creation(self):
        ordinary_file = self.root / "file.txt"
        ordinary_file.write_text("file", encoding="utf-8")
        for path in (
            self.root / "missing",
            ordinary_file,
            self.context_root,
            self.project_context_root
        ):
            with self.subTest(path=path):
                result, _rot_say, _rot_continue = self.run_add(
                    args=self.args(path=path)
                )
                self.assertEqual(result, 1)

    def test_bounded_inspection_excludes_dependencies_secrets_and_binaries(self):
        (self.project / "node_modules").mkdir()
        (self.project / ".cache").mkdir()
        (self.project / ".env").write_text("TOKEN=do-not-send", encoding="utf-8")
        (self.project / "private.key").write_text("private", encoding="utf-8")
        (self.project / "image.png").write_bytes(b"\x89PNG\0secret")

        required, optional = context_creation._inspect_project(
            self.project,
            ("github.com/example/project",)
        )

        rendered = repr((required, optional))
        self.assertNotIn("node_modules", rendered)
        self.assertNotIn(".cache", rendered)
        self.assertNotIn(".env", rendered)
        self.assertNotIn("private.key", rendered)
        self.assertNotIn("image.png", rendered)
        self.assertEqual(required[:2], ("main.py", "src/"))
        self.assertNotIn(str(self.project), required + optional)

    def test_bounded_inspection_skips_secrets_inside_approved_files(self):
        secret = "client_secret = super-sensitive-value"
        (self.project / "main.py").write_text(secret, encoding="utf-8")

        required, optional = context_creation._inspect_project(
            self.project,
            ("github.com/example/project",)
        )

        self.assertIn("main.py", required + optional)
        self.assertNotIn(secret, repr((required, optional)))

    def test_context_add_does_not_invoke_agent_and_creates_empty_namespaces(self):
        result, _rot_say, _rot_continue = self.run_add(answer="yes")

        self.assertEqual(result, 0)
        destination = self.project_context_root / "example"
        self.assertEqual(tuple((destination / "general").iterdir()), ())
        self.assertEqual(tuple((destination / "private").iterdir()), ())
        self.assertEqual(get_context_binding("example")["source_path"], str(self.project))

    def test_context_placeholder_documents_have_valid_headings(self):
        definition = SimpleNamespace(
            source=SimpleNamespace(is_git_repo=True, required_paths=["main.py", "src/"])
        )
        documents = context_creation._placeholder_documents("example", definition)
        self.assertEqual(documents["identity"].startswith("# example\n\n"), True)
        self.assertIn("Project known to RotBot", documents["identity"])
        self.assertEqual(documents["state"].startswith("# example State\n\n"), True)
        self.assertIn("Git-backed", documents["state"])
        self.assertIn("`main.py`", documents["state"])

    def test_context_is_created_bound_and_loadable_without_enrichment(self):
        destination = self.project_context_root / "example"

        result, _rot_say, _rot_continue = self.run_add(answer="yes")

        self.assertEqual(result, 0)
        self.assertTrue((destination / "identity.md").is_file())
        self.assertTrue((destination / "relationships.toml").is_file())
        self.assertTrue((destination / "match.toml").is_file())
        self.assertEqual(tuple((destination / "general").iterdir()), ())
        self.assertEqual(tuple((destination / "private").iterdir()), ())
        self.assertEqual(contexts.load_context("example").name, "example")

    def test_generated_identity_and_state_use_notes_and_bullet_points(self):
        documents = context_creation._placeholder_documents(
            "example",
            SimpleNamespace(
                source=SimpleNamespace(is_git_repo=True, required_paths=["main.py"])
            )
        )

        self.assertIn("# example", documents["identity"])
        self.assertIn("Project known to RotBot", documents["identity"])
        self.assertIn(context_creation.DOCUMENT_NOTES["state"], documents["state"])
        self.assertIn("- Context created by RotBot", documents["state"])

    def test_decline_happens_after_full_preview_and_writes_nothing(self):
        preview_seen = []

        def decline(_prompt):
            preview_seen.extend(
                call.args[0]
                for call in rot_say.call_args_list
            )
            return "n"

        with patch.object(context_creation, "rot_say") as rot_say, patch.object(
            context_creation,
            "rot_continue"
        ), patch("builtins.input", side_effect=decline):
            result = context_creation._add_project_context(self.args())

        self.assertEqual(result, 0)
        self.assertTrue(any("PROPOSED identity.md" in item for item in preview_seen))
        self.assertTrue(any("PROPOSED match.toml" in item for item in preview_seen))
        self.assertFalse((self.project_context_root / "example").exists())
        self.assertFalse(config_path().exists())

    def test_approved_creation_writes_project_files_and_registers_source(self):
        existing_config = config_path()
        existing_config.parent.mkdir(parents=True)
        existing_config.write_text('theme = "keep"\n', encoding="utf-8")

        result, _rot_say, rot_continue = self.run_add(answer="yes")

        self.assertEqual(result, 0)
        destination = self.project_context_root / "example"
        self.assertEqual(
            {path.name for path in destination.iterdir()},
            {"metadata.toml", "identity.md", "relationships.toml", "match.toml", "general", "private"}
        )
        self.assertEqual(tuple((destination / "general").iterdir()), ())
        self.assertEqual(tuple((destination / "private").iterdir()), ())
        loaded = contexts.load_context("example")
        self.assertEqual(loaded.identity, "# example\n\nProject known to RotBot.\n")
        self.assertEqual(loaded.state, "")
        match_text = (destination / "match.toml").read_text(encoding="utf-8")
        definition = context_matching.parse_match_toml(match_text)
        self.assertTrue(definition.source.is_git_repo)
        self.assertIn("github.com/example/project", definition.source.git_remotes)
        self.assertIn("main.py", definition.source.required_paths)
        self.assertIn("src/", definition.source.required_paths)
        self.assertIsNone(definition.production)
        self.assertNotIn(str(self.project), match_text)
        prompt = contexts.build_context_prompt("example")
        self.assertNotIn("github.com/example/project", prompt)
        self.assertNotIn(str(self.project), prompt)
        self.assertIn('theme = "keep"', existing_config.read_text(encoding="utf-8"))
        self.assertEqual(
            get_context_binding("example")["source_path"],
            str(self.project.resolve())
        )

        match = context_matching.match_contexts(
            self.project,
            name="example",
            binding_type="source",
            caddy_paths=()
        )[0]
        self.assertTrue(match.strong)

    def test_binding_failure_rolls_back_only_new_context(self):
        with patch.object(
            context_creation,
            "set_context_binding",
            side_effect=ConfigError("binding failed")
        ):
            result, _rot_say, _rot_continue = self.run_add(answer="yes")

        self.assertEqual(result, 1)
        self.assertFalse((self.project_context_root / "example").exists())
        self.assertFalse(config_path().exists())

    def test_file_failure_leaves_configuration_unchanged(self):
        config = config_path()
        config.parent.mkdir(parents=True)
        original = 'theme = "unchanged"\n'
        config.write_text(original, encoding="utf-8")

        with patch.object(
            context_creation,
            "_write_document",
            side_effect=OSError("write failed")
        ):
            result, _rot_say, _rot_continue = self.run_add(answer="yes")

        self.assertEqual(result, 1)
        self.assertFalse((self.project_context_root / "example").exists())
        self.assertEqual(config.read_text(encoding="utf-8"), original)

    def test_context_appearance_race_prevents_files_and_binding(self):
        destination = self.project_context_root / "example"

        def create_racing_context(_prompt):
            destination.mkdir()
            (destination / "marker").write_text("other process", encoding="utf-8")
            return "yes"

        with patch.object(context_creation, "rot_say"), patch.object(
            context_creation,
            "rot_continue"
        ), patch("builtins.input", side_effect=create_racing_context):
            result = context_creation._add_project_context(self.args())

        self.assertEqual(result, 1)
        self.assertEqual(
            (destination / "marker").read_text(encoding="utf-8"),
            "other process"
        )
        self.assertFalse(config_path().exists())

    def test_project_match_is_revalidated_after_confirmation(self):
        def change_remote(_prompt):
            subprocess.run(
                [
                    "git", "remote", "set-url", "upstream",
                    "git@github.com:example/different.git"
                ],
                cwd=self.project,
                check=True
            )
            return "yes"

        with patch.object(context_creation, "rot_say"), patch.object(
            context_creation,
            "rot_continue"
        ), patch("builtins.input", side_effect=change_remote):
            result = context_creation._add_project_context(self.args())

        self.assertEqual(result, 1)
        self.assertFalse((self.project_context_root / "example").exists())
        self.assertFalse(config_path().exists())

    def test_malformed_config_and_conflicting_binding_block_creation(self):
        config = config_path()
        config.parent.mkdir(parents=True)
        config.write_text("[broken", encoding="utf-8")
        result, _rot_say, _rot_continue = self.run_add(answer="yes")
        self.assertEqual(result, 1)

        config.write_text(
            "[contexts.example]\n"
            f'source_path = "{self.root / "other"}"\n',
            encoding="utf-8"
        )
        result, _rot_say, _rot_continue = self.run_add(answer="yes")
        self.assertEqual(result, 1)

    def test_git_project_without_remote_can_generate_path_match(self):
        subprocess.run(
            ["git", "remote", "remove", "upstream"],
            cwd=self.project,
            check=True
        )

        result, _rot_say, _rot_continue = self.run_add(answer="yes")

        self.assertEqual(result, 0)

    def test_non_git_project_can_generate_portable_match_and_binding(self):
        non_git = self.root / "plain-project"
        non_git.mkdir()
        (non_git / "project.toml").write_text("name = 'plain'\n", encoding="utf-8")
        (non_git / "src").mkdir()

        result, _rot_say, _rot_continue = self.run_add(
            answer="yes", args=self.args(name="plain", path=non_git)
        )

        self.assertEqual(result, 0)
        match_path = self.project_context_root / "plain" / "match.toml"
        content = match_path.read_text(encoding="utf-8")
        definition = context_matching.parse_match_toml(content)
        self.assertFalse(definition.source.is_git_repo)
        self.assertIn("project.toml", definition.source.required_paths)
        self.assertNotIn(str(non_git), content)
        self.assertEqual(get_context_binding("plain")["source_path"], str(non_git))

    def test_invalid_xdg_config_home_is_reported_before_creation(self):
        with patch.dict(os.environ, {"XDG_CONFIG_HOME": "relative"}, clear=True):
            result, _rot_say, _rot_continue = self.run_add(answer="yes")

        self.assertEqual(result, 1)


class ContextQuestionnaireTests(unittest.TestCase):
    def test_context_type_and_person_role_menus_can_exit(self):
        for answers in (
            ("exit",),
            ("4",),
            ("person", "alex", "q"),
            ("person", "alex", "4")
        ):
            with self.subTest(answers=answers), patch(
                "builtins.input",
                side_effect=answers
            ), patch.object(
                context_creation,
                "_add_project_context"
            ) as add_project, patch.object(
                context_creation,
                "_add_person_context"
            ) as add_person, patch.object(
                context_creation,
                "_add_machine_context"
            ) as add_machine, patch.object(context_creation, "rot_say"):
                result = context_creation.context_add(argparse.Namespace())

            self.assertEqual(result, 0)
            add_project.assert_not_called()
            add_person.assert_not_called()
            add_machine.assert_not_called()

    def test_project_questions_route_name_and_path(self):
        answers = ("project", "/srv/example", "example")

        with patch("builtins.input", side_effect=answers), patch.object(
            context_creation,
            "_add_project_context",
            return_value=7
        ) as add_project, patch.object(context_creation, "rot_say"):
            result = context_creation.context_add(argparse.Namespace())

        self.assertEqual(result, 7)
        project_args = add_project.call_args.args[0]
        self.assertEqual(project_args.name, "example")
        self.assertEqual(project_args.path, "/srv/example")

    def test_person_questions_route_role_and_display_name(self):
        answers = (
            "person", "alex", "user", "Alex Example", "1,2", "yes"
        )

        with patch("builtins.input", side_effect=answers), patch.object(
            context_creation.loader,
            "list_contexts",
            return_value=("rotbot", "signalrot")
        ), patch.object(
            context_creation.entities,
            "create_entity_context",
            return_value=Path("context/users/alex")
        ) as create_entity, patch.object(
            context_creation,
            "rot_say"
        ), patch.object(context_creation, "rot_continue"):
            result = context_creation.context_add(argparse.Namespace())

        self.assertEqual(result, 0)
        entity = create_entity.call_args.args[0]
        self.assertIsInstance(entity, context_creation.entities.UserContext)
        self.assertEqual(entity.name, "alex")
        self.assertEqual(entity.display_name, "Alex Example")
        self.assertEqual(entity.related_projects, ("rotbot", "signalrot"))

    def test_person_questions_route_assistant_role(self):
        answers = ("person", "rot", "assistant", "Rot", "", "yes")

        with patch("builtins.input", side_effect=answers), patch.object(
            context_creation.loader,
            "list_contexts",
            return_value=("rotbot", "signalrot")
        ), patch.object(
            context_creation.entities,
            "create_entity_context",
            return_value=Path("context/assistants/rot")
        ) as create_entity, patch.object(
            context_creation,
            "rot_say"
        ), patch.object(context_creation, "rot_continue"):
            result = context_creation.context_add(argparse.Namespace())

        self.assertEqual(result, 0)
        entity = create_entity.call_args.args[0]
        self.assertIsInstance(entity, context_creation.entities.AssistantContext)
        self.assertEqual((entity.name, entity.display_name), ("rot", "Rot"))

    def test_preselected_user_reuses_person_creation_workflow(self):
        answers = ("", "yes")
        args = argparse.Namespace(
            context_type="user",
            name="kamaji"
        )

        with patch("builtins.input", side_effect=answers), patch.object(
            context_creation.loader,
            "list_contexts",
            return_value=()
        ), patch.object(
            context_creation.entities,
            "create_entity_context",
            return_value=Path("context/users/kamaji")
        ) as create_entity, patch.object(
            context_creation,
            "rot_say"
        ), patch.object(context_creation, "rot_continue"):
            result = context_creation.context_add(args)

        self.assertEqual(result, 0)
        entity = create_entity.call_args.args[0]
        self.assertIsInstance(entity, context_creation.entities.UserContext)
        self.assertEqual((entity.name, entity.display_name), ("kamaji", "kamaji"))

    def test_person_questions_apply_contact_and_display_defaults(self):
        answers = ("2", "sam", "", "", "", "yes")

        with patch("builtins.input", side_effect=answers), patch.object(
            context_creation.loader,
            "list_contexts",
            return_value=("rotbot",)
        ), patch.object(
            context_creation.people,
            "create_person_context",
            return_value=Path("context/people/contact/sam")
        ) as create_person, patch.object(
            context_creation,
            "rot_say"
        ) as rot_say, patch.object(context_creation, "rot_continue"):
            result = context_creation.context_add(argparse.Namespace())

        self.assertEqual(result, 0)
        create_person.assert_called_once_with(
            "sam", "contact", "sam", related_projects=(), context_id=ANY
        )
        self.assertTrue(any(
            "Leave blank to use their context name: sam" in call.args[0]
            for call in rot_say.call_args_list
        ))

    def test_machine_leave_empty_skips_inspection_and_local_metadata(self):
        answers = ("machine", "desktop", "", "empty", "yes")

        with patch("builtins.input", side_effect=answers), patch.object(
            context_creation,
            "inspect_local_machine"
        ) as inspect, patch.object(
            context_creation.machines,
            "create_machine",
            return_value=Path("context/machines/desktop")
        ) as create_machine, patch.object(
            context_creation.machines,
            "create_local_machine_record"
        ) as create_local, patch.object(
            context_creation,
            "rot_say"
        ), patch.object(context_creation, "rot_continue"):
            result = context_creation.context_add(argparse.Namespace())

        self.assertEqual(result, 0)
        inspect.assert_not_called()
        create_machine.assert_called_once_with("desktop", "Desktop", None, ANY)
        create_local.assert_not_called()

    def test_machine_inspection_uses_approved_portable_and_declines_local_by_default(self):
        facts = MachineInspection(
            {"operating_system": "CachyOS", "architecture": "x86_64"},
            {"connection": {"hostname": "desktop-host"}}
        )
        answers = ("machine", "desktop", "Main Desktop", "inspect", "", "", "yes")

        with patch("builtins.input", side_effect=answers), patch.object(
            context_creation,
            "inspect_local_machine",
            return_value=facts
        ) as inspect, patch.object(
            context_creation,
            "show_inspection"
        ) as show, patch.object(
            context_creation.machines,
            "create_machine",
            return_value=Path("context/machines/desktop")
        ) as create_machine, patch.object(
            context_creation.machines,
            "create_local_machine_record"
        ) as create_local, patch.object(
            context_creation,
            "rot_say"
        ), patch.object(context_creation, "rot_continue"):
            result = context_creation.context_add(argparse.Namespace())

        self.assertEqual(result, 0)
        inspect.assert_called_once_with()
        show.assert_called_once_with(facts)
        create_machine.assert_called_once_with(
            "desktop", "Main Desktop", facts.portable, ANY
        )
        create_local.assert_not_called()

    def test_preselected_machine_name_can_approve_local_metadata(self):
        facts = MachineInspection(
            {"architecture": "x86_64"},
            {"connection": {"hostname": "desktop-host"}}
        )
        answers = ("", "inspect", "", "yes", "yes")
        args = argparse.Namespace(
            context_type="machine",
            name="desktop"
        )

        with patch("builtins.input", side_effect=answers), patch.object(
            context_creation,
            "inspect_local_machine",
            return_value=facts
        ), patch.object(context_creation, "show_inspection"), patch.object(
            context_creation.machines,
            "create_machine",
            return_value=Path("context/machines/desktop")
        ) as create_machine, patch.object(
            context_creation.machines,
            "create_local_machine_record",
            return_value=Path("config/rot/machines/desktop.toml")
        ) as create_local, patch.object(
            context_creation,
            "rot_say"
        ), patch.object(context_creation, "rot_continue"):
            result = context_creation.context_add(args)

        self.assertEqual(result, 0)
        create_machine.assert_called_once_with("desktop", "Desktop", facts.portable, ANY)
        create_local.assert_called_once_with("desktop", facts.local, ANY)

    def test_declined_portable_facts_are_not_written(self):
        facts = MachineInspection(
            {"operating_system": "CachyOS"},
            {}
        )
        answers = ("machine", "desktop", "", "inspect", "no", "yes")

        with patch("builtins.input", side_effect=answers), patch.object(
            context_creation,
            "inspect_local_machine",
            return_value=facts
        ), patch.object(context_creation, "show_inspection"), patch.object(
            context_creation.machines,
            "create_machine",
            return_value=Path("context/machines/desktop")
        ) as create_machine, patch.object(
            context_creation.machines,
            "create_local_machine_record"
        ) as create_local, patch.object(
            context_creation,
            "rot_say"
        ), patch.object(context_creation, "rot_continue"):
            result = context_creation.context_add(argparse.Namespace())

        self.assertEqual(result, 0)
        create_machine.assert_called_once_with("desktop", "Desktop", None, ANY)
        create_local.assert_not_called()

    def test_local_failure_preserves_created_portable_context(self):
        destination = Path("context/machines/desktop")
        facts = {"connection": {"hostname": "desktop-host"}}
        with patch("builtins.input", return_value="yes"), patch.object(
            context_creation.machines,
            "create_machine",
            return_value=destination
        ) as create_machine, patch.object(
            context_creation.machines,
            "create_local_machine_record",
            side_effect=context_creation.machines.MachineContextError("local failed")
        ), patch.object(context_creation, "rot_say") as rot_say, patch.object(
            context_creation,
            "rot_continue"
        ):
            result = context_creation._add_machine_context(
                "desktop", "Desktop", {}, facts, True
            )

        self.assertEqual(result, 1)
        create_machine.assert_called_once()
        self.assertIn("was created", rot_say.call_args.args[0])
        self.assertIn("local failed", rot_say.call_args.args[0])

    def test_declined_person_confirmation_creates_nothing(self):
        answers = ("person", "alex", "contact", "Alex", "", "no")

        with patch("builtins.input", side_effect=answers), patch.object(
            context_creation.loader,
            "list_contexts",
            return_value=("rotbot",)
        ), patch.object(
            context_creation.people,
            "create_person_context"
        ) as create_person, patch.object(
            context_creation,
            "rot_say"
        ), patch.object(context_creation, "rot_continue"):
            result = context_creation.context_add(argparse.Namespace())

        self.assertEqual(result, 0)
        create_person.assert_not_called()

    def test_related_project_menu_can_exit_without_creation(self):
        answers = ("person", "alex", "contact", "Alex", "exit")

        with patch("builtins.input", side_effect=answers), patch.object(
            context_creation.loader,
            "list_contexts",
            return_value=("rotbot", "signalrot")
        ), patch.object(
            context_creation,
            "_add_person_context"
        ) as add_person, patch.object(context_creation, "rot_say"):
            result = context_creation.context_add(argparse.Namespace())

        self.assertEqual(result, 0)
        add_person.assert_not_called()


if __name__ == "__main__":
    unittest.main()
