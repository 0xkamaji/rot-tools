import argparse
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from rotbot.contexts import machines
from rotbot.contexts import loader as contexts


MISSING = object()


class ContextLoaderTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name) / "context"
        self.root.mkdir()
        self.projects = self.root / "projects"
        self.projects.mkdir()
        self.people = self.root / "people"
        self.people.mkdir()
        from rotbot.contexts import people
        for role in people.PERSON_ROLES:
            (self.people / role).mkdir()
        self.machines = self.root / "machines"
        self.machines.mkdir()
        self.root_patch = patch.object(contexts, "CONTEXT_ROOT", self.root)
        self.root_patch.start()

    def tearDown(self):
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
        (directory / "identity.md").write_text(identity, encoding="utf-8")
        (directory / "state.md").write_text(state, encoding="utf-8")
        if vision is not MISSING:
            (directory / "vision.md").write_text(vision, encoding="utf-8")
        return directory

    def test_list_contexts_discovers_valid_directories_in_sorted_order(self):
        self.create_context("zeta", vision="future")
        self.create_context("alpha")
        (self.projects / "ordinary.txt").write_text("ignored", encoding="utf-8")
        (self.projects / "missing-state").mkdir()
        self.create_context(".hidden")

        self.assertEqual(contexts.list_contexts(), ("alpha", "zeta"))

    def test_empty_people_and_unknown_categories_are_not_contexts(self):
        self.assertEqual(contexts.list_contexts(), ())
        category = self.root / ".archive" / "projects"
        category.mkdir(parents=True)
        directory = category / "outsider"
        directory.mkdir()
        (directory / "identity.md").write_text("identity", encoding="utf-8")
        (directory / "state.md").write_text("state", encoding="utf-8")

        self.assertEqual(contexts.list_contexts(), ())
        with self.assertRaisesRegex(contexts.ContextError, "Unknown or invalid"):
            contexts.load_context("outsider")

    def test_context_list_renders_all_types_in_type_name_table(self):
        from rotbot.contexts import people

        self.create_context("zeta")
        self.create_context("alpha")
        people.create_person_context(
            "sam", "contact", "Sam Example", people_root=self.people
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
                ("person", "sam"),
                ("machine", "desktop")
            ),
            fill=False
        )

    def test_load_context_reads_identity_and_state(self):
        self.create_context("example", "identity\n", "state\n")

        loaded = contexts.load_context("example")

        self.assertEqual(loaded.name, "example")
        self.assertEqual(
            loaded,
            contexts.Context("example", "identity\n", "state\n")
        )

    def test_build_context_prompt_includes_both_files_as_read_only(self):
        self.create_context(
            "example",
            "identity text",
            "state text",
            vision="vision text"
        )

        prompt = contexts.build_context_prompt("example")

        self.assertIn("EXAMPLE CONTEXT IDENTITY (READ-ONLY)", prompt)
        self.assertIn("identity text", prompt)
        self.assertIn("EXAMPLE CONTEXT STATE (READ-ONLY)", prompt)
        self.assertIn("state text", prompt)
        self.assertNotIn("vision text", prompt)

    def test_load_context_does_not_read_or_return_vision(self):
        directory = self.create_context("example", "identity", "state")
        (directory / "vision.md").write_bytes(b"\xff")

        loaded = contexts.load_context("example")

        self.assertEqual(loaded, contexts.Context("example", "identity", "state"))
        self.assertFalse(hasattr(loaded, "vision"))

    def test_load_vision_distinguishes_present_missing_and_empty(self):
        self.create_context("present", vision="possible future")
        self.create_context("missing")
        self.create_context("empty", vision="")

        self.assertEqual(contexts.load_vision("present"), "possible future")
        self.assertIsNone(contexts.load_vision("missing"))
        self.assertEqual(contexts.load_vision("empty"), "")

    def test_load_vision_does_not_read_identity_or_state(self):
        directory = self.create_context("example", vision="vision only")
        (directory / "identity.md").write_bytes(b"\xff")
        (directory / "state.md").write_bytes(b"\xff")

        self.assertEqual(contexts.load_vision("example"), "vision only")

    def test_load_context_rejects_invalid_and_unknown_names(self):
        for name in (
            "../outside", "/tmp/outside", "nested/context",
            "projects/example", "."
        ):
            with self.subTest(name=name), self.assertRaises(contexts.ContextError):
                contexts.load_context(name)

        with self.assertRaisesRegex(contexts.ContextError, "Unknown or invalid"):
            contexts.load_context("does-not-exist")

    def test_load_vision_rejects_invalid_and_unknown_names(self):
        for name in ("../outside", "/tmp/outside", "nested/context", "."):
            with self.subTest(name=name), self.assertRaises(contexts.ContextError):
                contexts.load_vision(name)

        with self.assertRaisesRegex(contexts.ContextError, "Unknown or invalid"):
            contexts.load_vision("does-not-exist")

    def test_list_and_load_reject_context_symlink_outside_root(self):
        outside = Path(self.temporary_directory.name) / "outside"
        outside.mkdir()
        (outside / "identity.md").write_text("identity", encoding="utf-8")
        (outside / "state.md").write_text("state", encoding="utf-8")
        (self.projects / "linked").symlink_to(outside, target_is_directory=True)

        self.assertEqual(contexts.list_contexts(), ())
        with self.assertRaises(contexts.ContextError):
            contexts.load_context("linked")

    def test_load_vision_rejects_symlink_outside_context(self):
        directory = self.create_context("example")
        outside = Path(self.temporary_directory.name) / "outside-vision.md"
        outside.write_text("outside vision", encoding="utf-8")
        (directory / "vision.md").symlink_to(outside)

        self.assertEqual(contexts.list_contexts(), ("example",))
        with self.assertRaisesRegex(contexts.ContextError, "Invalid vision"):
            contexts.load_vision("example")

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
            (directory / "state.md").read_text(encoding="utf-8"),
            "state"
        )

    def test_show_handler_reports_unknown_context(self):
        with patch.object(contexts, "rot_say") as rot_say:
            result = contexts.context_show(argparse.Namespace(name="missing"))

        self.assertEqual(result, 1)
        self.assertIn("Unknown or invalid context", rot_say.call_args.args[0])

    def test_person_show_displays_only_populated_sections(self):
        from rotbot.contexts import people

        destination = people.create_person_context(
            "alex", "contact", "Alex Example", people_root=self.people
        )
        identity = destination / "identity.md"
        identity.write_text(
            identity.read_text(encoding="utf-8").replace(
                "<!-- Occupation, education, location, personal history, and "
                "other relevant life context. -->",
                "<!-- Occupation, education, location, personal history, and "
                "other relevant life context. -->\n\n- Grew up near the coast."
            ),
            encoding="utf-8"
        )

        with patch.object(contexts, "rot_say") as rot_say, patch.object(
            contexts,
            "rot_continue"
        ) as rot_continue:
            result = contexts.context_show(
                argparse.Namespace(name="alex", vision=False)
            )

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
        from rotbot.contexts import people

        people.create_person_context(
            "alex", "contact", "Alex", people_root=self.people
        )
        with patch.object(contexts, "rot_say"), patch.object(
            contexts,
            "rot_continue"
        ) as rot_continue:
            result = contexts.context_show(
                argparse.Namespace(name="alex", vision=False)
            )

        self.assertEqual(result, 0)
        output = rot_continue.call_args.args[0]
        self.assertIn("METADATA (metadata.toml; read-only)", output)
        self.assertIn("related_projects = []", output)
        self.assertTrue(output.endswith("(no recorded information)"))

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
            result = contexts.context_show(
                argparse.Namespace(name="desktop", vision=False)
            )

        self.assertEqual(result, 0)
        self.assertIn("MACHINE CONTEXT: desktop", rot_say.call_args.args[0])
        output = rot_continue.call_args.args[0]
        self.assertIn('operating_system = "CachyOS"', output)
        self.assertNotIn("private-host-sentinel", output)
        load_local.assert_not_called()

    def test_machine_vision_is_rejected(self):
        machines.create_machine("desktop", machines_root=self.machines)

        with patch.object(contexts, "rot_say") as rot_say:
            result = contexts.context_show(
                argparse.Namespace(name="desktop", vision=True)
            )

        self.assertEqual(result, 1)
        self.assertIn("only supported for project", rot_say.call_args.args[0])

    def test_show_without_name_lists_typed_contexts_and_uses_selection(self):
        from rotbot.contexts import people

        self.create_context("alpha")
        people.create_person_context(
            "alex", "contact", "Alex", people_root=self.people
        )
        with patch("builtins.input", return_value="2"), patch.object(
            contexts,
            "rot_say"
        ) as rot_say, patch.object(contexts, "rot_continue") as rot_continue:
            result = contexts.context_show(
                argparse.Namespace(name=None, vision=False)
            )

        self.assertEqual(result, 0)
        self.assertTrue(any(
            "1. project: alpha" in call.args[0]
            and "2. person: alex" in call.args[0]
            for call in rot_say.call_args_list
        ))
        self.assertTrue(
            rot_continue.call_args.args[0].endswith("(no recorded information)")
        )

    def test_show_selection_menu_can_exit_without_displaying(self):
        self.create_context("alpha")
        for answer in ("exit", "2", ""):
            with self.subTest(answer=answer), patch(
                "builtins.input",
                return_value=answer
            ), patch.object(contexts, "rot_say"), patch.object(
                contexts,
                "rot_continue"
            ) as rot_continue:
                result = contexts.context_show(
                    argparse.Namespace(name=None, vision=False)
                )

            self.assertEqual(result, 0)
            rot_continue.assert_not_called()

    def test_show_rejects_ambiguous_name_and_person_vision(self):
        from rotbot.contexts import people

        self.create_context("shared")
        people.create_person_context(
            "shared", "contact", "Shared", people_root=self.people
        )
        machines.create_machine("shared", machines_root=self.machines)
        people.create_person_context(
            "alex", "contact", "Alex", people_root=self.people
        )
        with patch.object(contexts, "rot_say") as rot_say:
            ambiguous = contexts.context_show(
                argparse.Namespace(name="shared", vision=False)
            )
            ambiguous_message = rot_say.call_args.args[0]
            person_vision = contexts.context_show(
                argparse.Namespace(name="alex", vision=True)
            )

        self.assertEqual(ambiguous, 1)
        self.assertIn("ambiguous", ambiguous_message)
        self.assertEqual(person_vision, 1)
        self.assertIn("only supported for project", rot_say.call_args.args[0])

    def test_standard_show_excludes_vision_when_present(self):
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
            result = contexts.context_show(
                argparse.Namespace(name="example", vision=False)
            )

        self.assertEqual(result, 0)
        output = rot_continue.call_args.args[0]
        self.assertIn("unique identity", output)
        self.assertIn("unique state", output)
        self.assertNotIn("unique vision", output)

    def test_vision_show_excludes_standard_context_and_includes_warning(self):
        self.create_context(
            "example",
            identity="unique identity",
            state="unique state content",
            vision="unique vision"
        )

        with patch.object(contexts, "rot_say") as rot_say, patch.object(
            contexts,
            "rot_continue"
        ) as rot_continue:
            result = contexts.context_show(
                argparse.Namespace(name="example", vision=True)
            )

        self.assertEqual(result, 0)
        self.assertIn("VISION", rot_say.call_args.args[0])
        output = rot_continue.call_args.args[0]
        self.assertIn("unique vision", output)
        self.assertNotIn("unique identity", output)
        self.assertNotIn("unique state content", output)
        self.assertIn("possible future direction", output)
        self.assertIn("not current state", output)
        self.assertIn("approved requirement", output)
        self.assertIn("authorization to implement", output)

    def test_vision_show_treats_missing_as_nonfatal_and_empty_as_present(self):
        self.create_context("missing")
        self.create_context("empty", vision="")

        with patch.object(contexts, "rot_say") as rot_say, patch.object(
            contexts,
            "rot_continue"
        ) as rot_continue:
            missing_result = contexts.context_show(
                argparse.Namespace(name="missing", vision=True)
            )
            missing_message = rot_say.call_args.args[0]
            rot_say.reset_mock()
            empty_result = contexts.context_show(
                argparse.Namespace(name="empty", vision=True)
            )

        self.assertEqual(missing_result, 0)
        self.assertEqual(
            missing_message,
            "No vision document exists for context 'missing'."
        )
        self.assertEqual(empty_result, 0)
        self.assertIn("VISION", rot_say.call_args.args[0])
        self.assertEqual(rot_continue.call_count, 1)

    def test_generic_context_operations_do_not_invoke_agents_or_modify_files(self):
        directory = self.create_context("example", "identity", "state", "vision")
        before = {
            path.name: path.read_bytes()
            for path in directory.iterdir()
        }

        with patch("rotbot.agents.runner.stream_agent") as stream_agent, patch.object(
            contexts,
            "rot_say"
        ), patch.object(contexts, "rot_continue"):
            contexts.list_contexts()
            contexts.load_context("example")
            contexts.load_vision("example")
            contexts.build_context_prompt("example")
            contexts.context_show(argparse.Namespace(name="example", vision=False))
            contexts.context_show(argparse.Namespace(name="example", vision=True))

        stream_agent.assert_not_called()
        after = {
            path.name: path.read_bytes()
            for path in directory.iterdir()
        }
        self.assertEqual(after, before)


if __name__ == "__main__":
    unittest.main()
