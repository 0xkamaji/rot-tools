import os
from pathlib import Path
import tempfile
import tomllib
import unittest

from rotbot.contexts import entities, people


class EntityContextTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name) / "context"
        for path in (
            self.root / "users", self.root / "assistants",
            self.root / "people" / "contact",
            self.root / "people" / "user",
            self.root / "people" / "assistant"
        ):
            path.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_user_and_assistant_are_distinct_first_class_types(self):
        user = entities.build_user_context("alex", context_id="00000000-0000-4000-8000-000000000001")
        assistant = entities.build_assistant_context("forge", context_id="00000000-0000-4000-8000-000000000002")
        user_path = entities.create_entity_context(user, root=self.root)
        assistant_path = entities.create_entity_context(assistant, root=self.root)

        self.assertIsInstance(entities.load_user_context(user.id, root=self.root), entities.UserContext)
        self.assertIsInstance(entities.load_assistant_context(assistant.id, root=self.root), entities.AssistantContext)
        self.assertEqual(tomllib.loads((user_path / "metadata.toml").read_text())["type"], "user")
        self.assertEqual(tomllib.loads((assistant_path / "metadata.toml").read_text())["type"], "assistant")
        self.assertTrue((assistant_path / "behavior.md").is_file())
        self.assertTrue((assistant_path / "capabilities.toml").is_file())
        self.assertFalse((user_path / "capabilities.toml").exists())

    def test_legacy_migration_preserves_id_content_and_is_idempotent(self):
        context_id = "00000000-0000-4000-8000-000000000003"
        source = people.create_person_context(
            "alex", "user", context_id=context_id,
            people_root=self.root / "people"
        )
        marker = "Legacy identity remains intact."
        (source / "identity.md").write_text(marker, encoding="utf-8")

        destination = entities.migrate_legacy_entity(
            "alex", entities.ContextType.USER, root=self.root
        )
        second = entities.migrate_legacy_entity(
            "alex", entities.ContextType.USER, root=self.root
        )

        self.assertEqual(destination, second)
        self.assertEqual(entities.load_user_context("alex", root=self.root).id, context_id)
        self.assertEqual((destination / "identity.md").read_text(), marker)
        self.assertFalse(source.exists())

    def test_assistant_migration_renames_preferences_to_behavior(self):
        source = people.create_person_context(
            "rot", "assistant", people_root=self.root / "people"
        )
        (source / "preferences.md").write_text("# Preferences\n\nBehavior marker", encoding="utf-8")

        destination = entities.migrate_legacy_entity(
            "rot", entities.ContextType.ASSISTANT, root=self.root
        )

        self.assertIn("Behavior marker", (destination / "behavior.md").read_text())
        self.assertFalse((destination / "preferences.md").exists())
        self.assertTrue((destination / "capabilities.toml").is_file())

    def test_migration_rejects_conflicting_destination(self):
        legacy = people.create_person_context(
            "alex", "user", context_id="00000000-0000-4000-8000-000000000004",
            people_root=self.root / "people"
        )
        canonical = entities.build_user_context(
            "alex", context_id="00000000-0000-4000-8000-000000000005"
        )
        files = entities.render_entity_files(canonical)
        destination = self.root / "users" / "alex"
        destination.mkdir()
        for filename, content in files.items():
            (destination / filename).write_text(content, encoding="utf-8")

        with self.assertRaisesRegex(entities.EntityContextError, "conflicts"):
            entities.migrate_legacy_entity("alex", "user", root=self.root)
        self.assertTrue(legacy.exists())

    def test_migration_rejects_symlinked_legacy_documents(self):
        source = people.create_person_context(
            "alex", "user", people_root=self.root / "people"
        )
        outside = self.root / "private.txt"
        outside.write_text("private", encoding="utf-8")
        (source / "identity.md").unlink()
        (source / "identity.md").symlink_to(outside)

        with self.assertRaisesRegex(entities.EntityContextError, "migration file"):
            entities.migrate_legacy_entity("alex", "user", root=self.root)
        self.assertFalse((self.root / "users" / "alex").exists())

    def test_canonical_duplicate_id_wins_over_legacy_copy(self):
        context_id = "00000000-0000-4000-8000-000000000006"
        people.create_person_context(
            "legacy", "user", context_id=context_id,
            people_root=self.root / "people"
        )
        entities.create_entity_context(
            entities.build_user_context("canonical", context_id=context_id),
            root=self.root
        )

        users = entities.list_user_contexts(root=self.root)
        self.assertEqual([(item.name, item.id) for item in users], [("canonical", context_id)])


if __name__ == "__main__":
    unittest.main()
