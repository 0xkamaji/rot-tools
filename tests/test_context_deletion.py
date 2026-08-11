import argparse
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from rotbot.contexts import deletion, loader, machines, people
from rotbot.contexts.config import ConfigError, get_context_binding, set_context_binding


class ContextDeletionTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.context_root = self.root / "context"
        self.context_root.mkdir()
        self.projects = self.context_root / "projects"
        self.projects.mkdir()
        self.people = self.context_root / "people"
        self.people.mkdir()
        for role in people.PERSON_ROLES:
            (self.people / role).mkdir()
        self.machines = self.context_root / "machines"
        self.machines.mkdir()
        self.config = self.root / "config" / "rotbot" / "config.toml"
        self.root_patch = patch.object(loader, "CONTEXT_ROOT", self.context_root)
        self.root_patch.start()

    def tearDown(self):
        self.root_patch.stop()
        self.temporary_directory.cleanup()

    def create_project(self, name="example", marker="original"):
        destination = self.projects / name
        destination.mkdir()
        (destination / "identity.md").write_text("identity", encoding="utf-8")
        (destination / "state.md").write_text("state", encoding="utf-8")
        (destination / "marker.txt").write_text(marker, encoding="utf-8")
        return destination

    def test_project_is_archived_outside_discovery_and_bindings_are_removed(self):
        source = self.create_project()
        set_context_binding("example", "source_path", "/old/source", self.config)
        set_context_binding("example", "production_path", "/old/site", self.config)

        context_type, destination = deletion.archive_context(
            "example",
            context_root=self.context_root,
            target_config=self.config
        )

        self.assertEqual(context_type, "project")
        self.assertFalse(source.exists())
        self.assertEqual(destination.name, "payload")
        self.assertEqual(
            destination.parents[2],
            self.context_root / ".archive" / "projects"
        )
        self.assertEqual(
            (destination / "marker.txt").read_text(encoding="utf-8"),
            "original"
        )
        self.assertEqual(get_context_binding("example", self.config), {})
        self.assertEqual(loader.list_contexts(), ())
        with self.assertRaises(loader.ContextError):
            loader.load_context("example")

    def test_person_is_archived_without_changing_same_named_project_bindings(self):
        source = people.create_person_context(
            "alex", "contact", "Alex", people_root=self.people
        )
        set_context_binding("alex", "source_path", "/keep/project", self.config)

        context_type, destination = deletion.archive_context(
            "alex",
            context_root=self.context_root,
            target_config=self.config
        )

        self.assertEqual(context_type, "person")
        self.assertFalse(source.exists())
        self.assertEqual(
            destination.parents[2],
            self.context_root / ".archive" / "contacts"
        )
        self.assertEqual(
            get_context_binding("alex", self.config)["source_path"],
            "/keep/project"
        )

    def test_each_person_role_uses_its_own_archive_bucket(self):
        expected = {
            "contact": "contacts",
            "user": "users",
            "assistant": "assistants"
        }
        for role, bucket in expected.items():
            with self.subTest(role=role):
                name = f"example-{role}"
                people.create_person_context(name, role, people_root=self.people)
                _context_type, destination = deletion.archive_context(
                    name,
                    context_root=self.context_root,
                    target_config=self.config
                )
                self.assertEqual(
                    destination.parents[2],
                    self.context_root / ".archive" / bucket
                )

    def test_machine_is_archived_without_changing_same_named_project_bindings(self):
        source = machines.create_machine(
            "desktop", machines_root=self.machines
        )
        project = self.create_project("desktop")
        set_context_binding("desktop", "source_path", "/keep/project", self.config)
        local = machines.create_local_machine_record(
            "desktop",
            {"connection": {"hostname": "desktop-host"}},
            target_config=self.config
        )

        with self.assertRaisesRegex(deletion.ContextDeletionError, "ambiguous"):
            deletion.archive_context(
                "desktop",
                context_root=self.context_root,
                target_config=self.config
            )

        context_type, destination = deletion.archive_context(
            "desktop",
            context_type="machine",
            context_root=self.context_root,
            target_config=self.config
        )

        self.assertEqual(context_type, "machine")
        self.assertFalse(source.exists())
        self.assertTrue(project.exists())
        self.assertTrue(local.exists())
        self.assertEqual(
            destination.parents[2],
            self.context_root / ".archive" / "machines"
        )
        self.assertEqual(
            get_context_binding("desktop", self.config)["source_path"],
            "/keep/project"
        )
        self.assertEqual(
            machines.list_machine_contexts(machines_root=self.machines),
            ()
        )

    def test_recreated_context_produces_a_separate_archive_record(self):
        self.create_project(marker="first")
        _, first = deletion.archive_context(
            "example", context_root=self.context_root, target_config=self.config
        )
        self.create_project(marker="second")
        _, second = deletion.archive_context(
            "example", context_root=self.context_root, target_config=self.config
        )

        self.assertNotEqual(first.parent, second.parent)
        self.assertEqual((first / "marker.txt").read_text(encoding="utf-8"), "first")
        self.assertEqual((second / "marker.txt").read_text(encoding="utf-8"), "second")

    def test_invalid_unknown_ambiguous_and_symlink_contexts_are_rejected(self):
        for name in ("", "../outside", "nested/name", ".hidden"):
            with self.subTest(name=name), self.assertRaises(deletion.ContextDeletionError):
                deletion.archive_context(
                    name, context_root=self.context_root, target_config=self.config
                )
        with self.assertRaises(deletion.ContextDeletionError):
            deletion.archive_context(
                "missing", context_root=self.context_root, target_config=self.config
            )

        self.create_project("shared")
        people.create_person_context("shared", "contact", people_root=self.people)
        with self.assertRaisesRegex(deletion.ContextDeletionError, "ambiguous"):
            deletion.archive_context(
                "shared", context_root=self.context_root, target_config=self.config
            )
        self.assertTrue((self.projects / "shared").exists())
        self.assertTrue((self.people / "contact" / "shared").exists())

        outside = self.root / "outside"
        outside.mkdir()
        (self.projects / "linked").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(deletion.ContextDeletionError):
            deletion.archive_context(
                "linked", context_root=self.context_root, target_config=self.config
            )

        archive_target = self.root / "archive-target"
        archive_target.mkdir()
        (self.context_root / ".archive").symlink_to(
            archive_target,
            target_is_directory=True
        )
        self.create_project("safe")
        with self.assertRaises(deletion.ContextDeletionError):
            deletion.archive_context(
                "safe", context_root=self.context_root, target_config=self.config
            )
        self.assertTrue((self.projects / "safe").exists())

    def test_explicit_type_resolves_same_name_project_and_person(self):
        self.create_project("shared")
        people.create_person_context("shared", "contact", people_root=self.people)

        context_type, _destination = deletion.archive_context(
            "shared",
            context_type="person",
            context_root=self.context_root,
            target_config=self.config
        )

        self.assertEqual(context_type, "person")
        self.assertTrue((self.projects / "shared").exists())
        self.assertFalse((self.people / "contact" / "shared").exists())

    def test_binding_failure_rolls_archived_project_back(self):
        source = self.create_project()

        with patch.object(
            deletion,
            "remove_context_bindings",
            side_effect=ConfigError("config failed")
        ), self.assertRaises(deletion.ContextDeletionError):
            deletion.archive_context(
                "example", context_root=self.context_root, target_config=self.config
            )

        self.assertTrue(source.is_dir())
        self.assertEqual(
            (source / "marker.txt").read_text(encoding="utf-8"),
            "original"
        )
        records = tuple(
            (self.context_root / ".archive" / "projects" / "example").iterdir()
        )
        self.assertEqual(records, ())

    def test_cli_cancellation_changes_nothing(self):
        source = self.create_project()

        with patch("builtins.input", return_value="no"), patch.object(
            deletion,
            "rot_say"
        ), patch.object(deletion, "rot_continue"):
            result = deletion.context_delete(argparse.Namespace(name="example"))

        self.assertEqual(result, 0)
        self.assertTrue(source.exists())
        self.assertFalse((self.context_root / ".archive").exists())

    def test_no_name_lists_all_contexts_and_archives_numbered_selection(self):
        self.create_project("zeta")
        people.create_person_context("alex", "contact", people_root=self.people)

        with patch("builtins.input", side_effect=("2", "yes")), patch.object(
            deletion,
            "archive_context",
            return_value=("person", Path(".archive/contacts/alex"))
        ) as archive_context, patch.object(
            deletion,
            "rot_say"
        ) as rot_say, patch.object(deletion, "rot_continue"):
            result = deletion.context_delete(argparse.Namespace(name=None))

        self.assertEqual(result, 0)
        archive_context.assert_called_once_with("alex", context_type="person")
        self.assertTrue(any(
            "1. project: zeta" in call.args[0]
            and "2. person: alex" in call.args[0]
            for call in rot_say.call_args_list
        ))

    def test_delete_selection_menu_can_exit_without_archiving(self):
        self.create_project("example")
        for answer in ("exit", "2", ""):
            with self.subTest(answer=answer), patch(
                "builtins.input",
                return_value=answer
            ), patch.object(
                deletion,
                "archive_context"
            ) as archive_context, patch.object(deletion, "rot_say"):
                result = deletion.context_delete(argparse.Namespace(name=None))

            self.assertEqual(result, 0)
            archive_context.assert_not_called()

    def test_deletable_listing_includes_safe_directories_even_if_incomplete(self):
        self.create_project("valid")
        incomplete = self.people / "contact" / "incomplete"
        incomplete.mkdir()
        (self.machines / "unconfigured").mkdir()
        ordinary = self.projects / "ordinary.txt"
        ordinary.write_text("not a context", encoding="utf-8")

        self.assertEqual(
            deletion.list_deletable_contexts(context_root=self.context_root),
            (
                ("project", "valid"),
                ("person", "incomplete"),
                ("machine", "unconfigured")
            )
        )

    def test_duplicate_person_name_across_roles_is_rejected(self):
        people.create_person_context("alex", "contact", people_root=self.people)
        (self.people / "assistant" / "alex").mkdir()

        with self.assertRaisesRegex(deletion.ContextDeletionError, "multiple role"):
            deletion.archive_context(
                "alex", context_root=self.context_root, target_config=self.config
            )
        with self.assertRaisesRegex(deletion.ContextDeletionError, "multiple role"):
            deletion.list_deletable_contexts(context_root=self.context_root)


if __name__ == "__main__":
    unittest.main()
