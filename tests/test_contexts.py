import argparse
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from rotbot.contexts import entities, machines, people
from rotbot.contexts import loader as contexts


MISSING = object()


class ContextLoaderTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name) / "context"
        self.root.mkdir()
        self.projects = self.root / "projects"
        self.projects.mkdir()
        self.contacts = self.root / "contacts"
        self.contacts.mkdir()
        self.users = self.root / "users"
        self.users.mkdir()
        self.assistants = self.root / "assistants"
        self.assistants.mkdir()
        self.machines = self.root / "machines"
        self.machines.mkdir()
        self.root_patch = patch.object(contexts, "CONTEXT_ROOT", self.root)
        self.root_patch.start()
        self.builtins_patch = patch.object(
            entities,
            "builtin_assistants_root",
            return_value=self.root / ".builtins" / "assistants"
        )
        self.builtins_patch.start()

    def tearDown(self):
        self.builtins_patch.stop()
        self.root_patch.stop()
        self.temporary_directory.cleanup()

    def create_context(
        self,
        name,
        identity="identity text",
        state="state text",
        vision=MISSING
    ):
        directory = self.projects / name
        directory.mkdir()
        (directory / "metadata.toml").write_text(
            contexts.render_project_metadata(name), encoding="utf-8"
        )
        (directory / "general").mkdir()
        private = directory / "private"
        private.mkdir()
        (directory / "identity.md").write_text(identity, encoding="utf-8")
        (directory / "relationships.toml").write_text("", encoding="utf-8")
        (private / "state.md").write_text(state, encoding="utf-8")
        if vision is not MISSING:
            (private / "vision.md").write_text(vision, encoding="utf-8")
        return directory

    def test_list_contexts_discovers_valid_directories_in_sorted_order(self):
        self.create_context("zeta", vision="future")
        self.create_context("alpha")
        (self.projects / "ordinary.txt").write_text("ignored", encoding="utf-8")
        (self.projects / "missing-state").mkdir()
        (self.projects / ".hidden").mkdir()

        self.assertEqual(contexts.list_contexts(), ("alpha", "zeta"))

    def test_empty_canonical_categories_and_unknown_categories_are_not_contexts(self):
        self.assertEqual(contexts.list_contexts(), ())
        category = self.root / ".archive" / "projects"
        category.mkdir(parents=True)
        directory = category / "outsider"
        directory.mkdir()
        (directory / "general").mkdir()
        (directory / "private").mkdir()
        (directory / "identity.md").write_text("identity", encoding="utf-8")
        (directory / "relationships.toml").write_text("", encoding="utf-8")

        self.assertEqual(contexts.list_contexts(), ())
        with self.assertRaisesRegex(contexts.ContextError, "Unknown or invalid"):
            contexts.load_context("outsider")

    def test_context_list_renders_all_types_in_type_name_table(self):
        self.create_context("zeta")
        self.create_context("alpha")
        people.create_person_context("sam", "contact", "Sam Example")
        entities.create_entity_context(
            entities.build_user_context("kamaji", "Kamaji"), root=self.root
        )
        entities.create_entity_context(
            entities.build_assistant_context("forge", "Forge"), root=self.root
        )
        machines.create_machine("desktop", machines_root=self.machines)

        with patch.object(contexts, "rot_say") as rot_say, patch.object(
            contexts,
            "rot_table"
        ) as rot_table:
            result = contexts.context_list(argparse.Namespace())

        self.assertEqual(result, 0)
        rot_say.assert_called_once_with("CONTEXTS")
        rot_table.assert_called_once_with(
            ("TYPE", "NAME"),
            (
                ("project", "alpha"),
                ("project", "zeta"),
                ("user", "kamaji"),
                ("assistant", "forge"),
                ("contact", "sam"),
                ("machine", "desktop")
            ),
            fill=False
        )

    def test_load_context_reads_identity_and_state(self):
        self.create_context("example", "identity\n", "state\n")

        loaded = contexts.load_context("example")

        self.assertEqual(loaded.name, "example")
        self.assertEqual(loaded.name, "example")
        self.assertEqual(loaded.identity, "identity\n")
        self.assertEqual(loaded.state, "state\n")
        self.assertIsNotNone(loaded.id)

    def test_project_context_can_be_loaded_by_stable_id(self):
        directory = self.create_context("example")
        (directory / "metadata.toml").write_text(
            contexts.render_project_metadata(
                "example",
                "00000000-0000-4000-8000-000000000001"
            ),
            encoding="utf-8"
        )

        loaded = contexts.load_context_reference(
            "00000000-0000-4000-8000-000000000001"
        )

        self.assertEqual(loaded.name, "example")

    def test_build_context_prompt_includes_identity_and_knowledge_as_read_only(self):
        self.create_context(
            "example",
            "identity text",
            "state text",
            vision="vision text"
        )

        prompt = contexts.build_context_prompt("example")

        self.assertIn("EXAMPLE CONTEXT IDENTITY (READ-ONLY)", prompt)
        self.assertIn("identity text", prompt)
        self.assertIn("EXAMPLE CONTEXT KNOWLEDGE (READ-ONLY)", prompt)
        self.assertIn("state text", prompt)
        self.assertIn("[private/state.md]", prompt)
        self.assertIn("[private/vision.md]", prompt)

    def test_load_context_discovers_vision_by_selected_view(self):
        directory = self.create_context("example", vision="private vision")
        (directory / "general" / "vision.md").write_text(
            "general vision", encoding="utf-8"
        )

        full = contexts.load_context("example", view="full")
        egress = contexts.load_context("example", view="egress")

        self.assertIn(
            contexts.ProjectDocument("vision.md", "general", "general vision"),
            full.knowledge
        )
        self.assertIn(
            contexts.ProjectDocument("vision.md", "private", "private vision"),
            full.knowledge
        )
        self.assertEqual(
            tuple(document for document in egress.knowledge
                  if document.filename == "vision.md"),
            (contexts.ProjectDocument("vision.md", "general", "general vision"),)
        )
        self.assertFalse(hasattr(full, "vision"))

    def test_load_context_rejects_invalid_and_unknown_names(self):
        for name in (
            "../outside", "/tmp/outside", "nested/context",
            "projects/example", "."
        ):
            with self.subTest(name=name), self.assertRaises(contexts.ContextError):
                contexts.load_context(name)

        with self.assertRaisesRegex(contexts.ContextError, "Unknown or invalid"):
            contexts.load_context("does-not-exist")

    def test_list_and_load_reject_context_symlink_outside_root(self):
        outside = Path(self.temporary_directory.name) / "outside"
        outside.mkdir()
        (outside / "general").mkdir()
        (outside / "private").mkdir()
        (outside / "metadata.toml").write_text(
            contexts.render_project_metadata("linked"), encoding="utf-8"
        )
        (outside / "identity.md").write_text("identity", encoding="utf-8")
        (outside / "relationships.toml").write_text("", encoding="utf-8")
        (self.projects / "linked").symlink_to(outside, target_is_directory=True)

        self.assertEqual(contexts.list_contexts(), ())
        with self.assertRaises(contexts.ContextError):
            contexts.load_context("linked")

    def test_load_context_rejects_symlinked_vision_knowledge(self):
        directory = self.create_context("example")
        outside = Path(self.temporary_directory.name) / "outside-vision.md"
        outside.write_text("outside vision", encoding="utf-8")
        (directory / "private" / "vision.md").symlink_to(outside)

        self.assertEqual(contexts.list_contexts(), ("example",))
        with self.assertRaisesRegex(contexts.ContextError, "Invalid knowledge document"):
            contexts.load_context("example")

    def test_show_handler_displays_loaded_files_without_modifying_them(self):
        directory = self.create_context("example", "identity", "state")
        args = argparse.Namespace(name="example")

        with patch.object(contexts, "rot_say") as rot_say, patch.object(
            contexts,
            "rot_continue"
        ) as rot_continue:
            self.assertEqual(contexts.context_show(args), 0)

        self.assertIn("example", rot_say.call_args.args[0])
        self.assertIn("identity", rot_continue.call_args.args[0])
        self.assertIn("state", rot_continue.call_args.args[0])
        self.assertEqual(
            (directory / "identity.md").read_text(encoding="utf-8"),
            "identity"
        )
        self.assertEqual(
            (directory / "private" / "state.md").read_text(encoding="utf-8"),
            "state"
        )

    def test_show_handler_reports_unknown_context(self):
        with patch.object(contexts, "rot_say") as rot_say:
            result = contexts.context_show(argparse.Namespace(name="missing"))

        self.assertEqual(result, 1)
        self.assertIn("Unknown or invalid context", rot_say.call_args.args[0])

    def test_person_show_displays_only_populated_sections(self):
        destination = people.create_person_context(
            "alex", "contact", "Alex Example"
        )
        identity = destination / "private" / "biography.md"
        identity.write_text(
            "# Biography\n\n## Background\n\n- Grew up near the coast.\n",
            encoding="utf-8"
        )

        with patch.object(contexts, "rot_say") as rot_say, patch.object(
            contexts,
            "rot_continue"
        ) as rot_continue:
            result = contexts.context_show(argparse.Namespace(name="alex"))

        self.assertEqual(result, 0)
        self.assertIn("PERSON CONTEXT: alex (Alex Example)", rot_say.call_args.args[0])
        output = rot_continue.call_args.args[0]
        self.assertIn("IDENTITY (identity.md; read-only)", output)
        self.assertIn("## Background", output)
        self.assertIn("Grew up near the coast", output)
        self.assertIn("METADATA (metadata.toml; read-only)", output)
        self.assertIn('type = "person"', output)
        self.assertIn('role = "contact"', output)
        self.assertIn('name = "alex"', output)
        self.assertIn('display_name = "Alex Example"', output)
        self.assertIn("related_projects = []", output)
        self.assertNotIn("## Skills and Knowledge", output)
        self.assertNotIn("PREFERENCES", output)
        self.assertNotIn("<!--", output)

    def test_empty_person_show_reports_no_recorded_information(self):
        people.create_person_context("alex", "contact", "Alex")
        with patch.object(contexts, "rot_say"), patch.object(
            contexts,
            "rot_continue"
        ) as rot_continue:
            result = contexts.context_show(argparse.Namespace(name="alex"))

        self.assertEqual(result, 0)
        output = rot_continue.call_args.args[0]
        self.assertIn("METADATA (metadata.toml; read-only)", output)
        self.assertIn("related_projects = []", output)
        self.assertIn("IDENTITY (identity.md; read-only)", output)
        self.assertIn("Person known to the RotBot user", output)

    def test_machine_show_loads_only_portable_files(self):
        destination = machines.create_machine(
            "desktop",
            "Main Desktop",
            {"operating_system": "CachyOS"},
            machines_root=self.machines
        )
        local = machines.create_local_machine_record(
            "desktop",
            {"connection": {"hostname": "private-host-sentinel"}},
            target_config=Path(self.temporary_directory.name) / "config" / "config.toml"
        )

        with patch.object(
            machines,
            "load_local_machine_record"
        ) as load_local, patch.object(contexts, "rot_say") as rot_say, patch.object(
            contexts,
            "rot_continue"
        ) as rot_continue:
            result = contexts.context_show(argparse.Namespace(name="desktop"))

        self.assertEqual(result, 0)
        self.assertIn("MACHINE CONTEXT: desktop", rot_say.call_args.args[0])
        output = rot_continue.call_args.args[0]
        self.assertIn('operating_system = "CachyOS"', output)
        self.assertNotIn("private-host-sentinel", output)
        load_local.assert_not_called()

    def test_show_without_name_lists_typed_contexts_and_uses_selection(self):
        self.create_context("alpha")
        people.create_person_context("alex", "contact", "Alex")
        with patch("builtins.input", side_effect=("2", "2")), patch.object(
            contexts,
            "rot_say"
        ) as rot_say, patch.object(contexts, "rot_continue") as rot_continue:
            result = contexts.context_show(argparse.Namespace(name=None))

        self.assertEqual(result, 0)
        self.assertTrue(any(
            "1. project: alpha" in call.args[0]
            and "2. contact: alex" in call.args[0]
            for call in rot_say.call_args_list
        ))
        self.assertIn("Person known to the RotBot user", rot_continue.call_args.args[0])

    def test_show_without_name_can_display_current_session_read_only(self):
        from rotbot.contexts import inspection

        inspected = inspection.InspectedContext(
            "rot",
            "00000000-0000-4000-8000-000000000001",
            "kamaji",
            "00000000-0000-4000-8000-000000000002",
            "laptop",
            "00000000-0000-4000-8000-000000000003",
            "rotbot",
            "00000000-0000-4000-8000-000000000004",
            Path("/srv/rotbot"),
            inspection.IdentificationSources(
                "local config",
                "local config",
                "local config",
                "source binding"
            ),
            ()
        )
        with patch("builtins.input", return_value="1"), patch.object(
            inspection,
            "inspect_current_context",
            return_value=inspected
        ) as inspect, patch.object(contexts, "rot_say") as rot_say:
            result = contexts.context_show(argparse.Namespace(name=None))

        self.assertEqual(result, 0)
        inspect.assert_called_once_with(bootstrap=False)
        self.assertIn("CURRENT ROTBOT CONTEXT", rot_say.call_args.args[0])
        self.assertIn("Project:    rotbot", rot_say.call_args.args[0])

    def test_show_selection_menu_can_exit_without_displaying(self):
        self.create_context("alpha")
        for answer in ("exit", "3", ""):
            with self.subTest(answer=answer), patch(
                "builtins.input",
                return_value=answer
            ), patch.object(contexts, "rot_say"), patch.object(
                contexts,
                "rot_continue"
            ) as rot_continue:
                result = contexts.context_show(argparse.Namespace(name=None))

            self.assertEqual(result, 0)
            rot_continue.assert_not_called()

    def test_existing_scope_reports_when_no_saved_contexts_exist(self):
        with patch("builtins.input", return_value="2"), patch.object(
            contexts,
            "rot_say"
        ) as rot_say:
            result = contexts.context_show(argparse.Namespace(name=None))

        self.assertEqual(result, 1)
        self.assertIn("No saved contexts", rot_say.call_args.args[0])

    def test_show_rejects_ambiguous_name(self):
        self.create_context("shared")
        people.create_person_context("shared", "contact", "Shared")
        machines.create_machine("shared", machines_root=self.machines)
        with patch.object(contexts, "rot_say") as rot_say:
            result = contexts.context_show(argparse.Namespace(name="shared"))

        self.assertEqual(result, 1)
        self.assertIn("ambiguous", rot_say.call_args.args[0])

    def test_standard_show_includes_dynamically_discovered_vision(self):
        self.create_context(
            "example",
            identity="unique identity",
            state="unique state",
            vision="unique vision"
        )

        with patch.object(contexts, "rot_say"), patch.object(
            contexts,
            "rot_continue"
        ) as rot_continue:
            result = contexts.context_show(argparse.Namespace(name="example"))

        self.assertEqual(result, 0)
        output = rot_continue.call_args.args[0]
        self.assertIn("unique identity", output)
        self.assertIn("unique state", output)
        self.assertIn("unique vision", output)
        self.assertIn("private/vision.md", output)

    def test_generic_context_operations_do_not_invoke_agents_or_modify_files(self):
        directory = self.create_context("example", "identity", "state", "vision")
        before = {
            path.relative_to(directory): path.read_bytes()
            for path in directory.rglob("*") if path.is_file()
        }

        with patch("rotbot.agents.invocation.invoke") as stream_agent, patch.object(
            contexts,
            "rot_say"
        ), patch.object(contexts, "rot_continue"):
            contexts.list_contexts()
            contexts.load_context("example")
            contexts.build_context_prompt("example")
            contexts.context_show(argparse.Namespace(name="example"))

        stream_agent.assert_not_called()
        after = {
            path.relative_to(directory): path.read_bytes()
            for path in directory.rglob("*") if path.is_file()
        }
        self.assertEqual(after, before)


if __name__ == "__main__":
    unittest.main()
