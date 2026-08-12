from pathlib import Path
import tempfile
import tomllib
import unittest

from rotbot.contexts import entities


class EntityContextTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name) / "context"
        self.root.mkdir()

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
        self.assertTrue((assistant_path / "local" / "behavior.md").is_file())
        self.assertTrue((user_path / "local" / "identity.md").is_file())
        self.assertFalse((user_path / "identity.md").exists())
        self.assertFalse((assistant_path / "behavior.md").exists())
        self.assertTrue((user_path / "shareable").is_dir())
        self.assertTrue((assistant_path / "shareable").is_dir())
        self.assertTrue((assistant_path / "capabilities.toml").is_file())
        self.assertFalse((user_path / "capabilities.toml").exists())

    def test_canonical_entities_load_by_name_and_id_and_list_by_name(self):
        first = entities.build_user_context(
            "zeta", context_id="00000000-0000-4000-8000-000000000003"
        )
        second = entities.build_user_context(
            "alpha", context_id="00000000-0000-4000-8000-000000000004"
        )
        entities.create_entity_context(first, root=self.root)
        entities.create_entity_context(second, root=self.root)

        self.assertEqual(entities.load_user_context("zeta", root=self.root), first)
        self.assertEqual(entities.load_user_context(second.id, root=self.root), second)
        self.assertEqual(
            [item.name for item in entities.list_user_contexts(root=self.root)],
            ["alpha", "zeta"]
        )

    def test_entity_documents_union_local_and_shareable_semantics(self):
        user = entities.build_user_context(
            "alex", context_id="00000000-0000-4000-8000-000000000005"
        )
        destination = entities.create_entity_context(user, root=self.root)
        (destination / "local" / "identity.md").write_text(
            "# Identity\n\n## Local\n\nPrivate identity.\n", encoding="utf-8"
        )
        (destination / "shareable" / "experience.md").write_text(
            "# Experience\n\n## Shared\n\nPublic experience.\n", encoding="utf-8"
        )

        _user, documents = entities.load_user_documents(user.id, root=self.root)

        rendered = repr(documents)
        self.assertIn("Private identity", rendered)
        self.assertIn("Public experience", rendered)


if __name__ == "__main__":
    unittest.main()
