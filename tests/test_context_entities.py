from pathlib import Path
import tempfile
import tomllib
import unittest
from unittest.mock import patch

from rotbot.agents import invocation
from rotbot.agents.config import OPENCODE
from rotbot.contexts import entities, inspection, loader


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


class AssistantLayeringTests(unittest.TestCase):
    ASSISTANT_ID = "00000000-0000-4000-8000-000000000006"

    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.context_root = self.root / "contexts"
        self.context_root.mkdir()
        self.builtin_root = self.root / "builtin" / "assistants"
        self.builtin_root.mkdir(parents=True)
        self.context_patch = patch.object(loader, "CONTEXT_ROOT", self.context_root)
        self.builtin_patch = patch.object(
            entities, "builtin_assistants_root", return_value=self.builtin_root
        )
        self.context_patch.start()
        self.builtin_patch.start()

    def tearDown(self):
        self.builtin_patch.stop()
        self.context_patch.stop()
        self.temporary_directory.cleanup()

    def create_builtin(self, context_id=None):
        assistant = entities.build_assistant_context(
            "rot", "Rot", context_id=context_id or self.ASSISTANT_ID
        )
        destination = self.builtin_root / "rot"
        destination.mkdir()
        (destination / "shareable").mkdir()
        (destination / "metadata.toml").write_text(
            entities.render_metadata(assistant), encoding="utf-8"
        )
        (destination / "capabilities.toml").write_text(
            entities.SAFE_CAPABILITIES, encoding="utf-8"
        )
        self.write(destination / "shareable", "identity.md", "Builtin identity")
        self.write(destination / "shareable", "behavior.md", "Builtin behavior")
        self.write(
            destination / "shareable", "relationship.md", "Builtin relationship"
        )
        return assistant

    def create_local(self):
        assistant = entities.build_assistant_context(
            "rot", "Rot", context_id=self.ASSISTANT_ID
        )
        destination = entities.create_entity_context(assistant)
        return assistant, destination

    def write(self, directory, filename, text):
        directory.joinpath(filename).write_text(
            f"# {filename.removesuffix('.md').title()}\n\n## Details\n\n{text}\n",
            encoding="utf-8"
        )

    def loaded(self, view="full"):
        _assistant, documents = entities.load_assistant_documents("rot", view=view)
        return {document.filename: document for document in documents}

    def test_builtin_only_assistant_loads_each_builtin_document_once(self):
        self.create_builtin()
        loaded = self.loaded()

        self.assertEqual(
            tuple(loaded), ("behavior.md", "identity.md", "relationship.md")
        )
        self.assertIn("Builtin identity", repr(loaded["identity.md"]))

    def test_local_only_assistant_loads_local_documents(self):
        _assistant, destination = self.create_local()
        self.write(destination / "shareable", "state.md", "Local state")

        loaded = self.loaded("egress")

        self.assertEqual(tuple(loaded), ("state.md",))
        self.assertIn("Local state", repr(loaded["state.md"]))

    def test_populated_local_documents_override_defaults_without_duplicates(self):
        self.create_builtin()
        _assistant, destination = self.create_local()
        self.write(destination / "shareable", "behavior.md", "Local behavior")
        self.write(destination / "shareable", "identity.md", "Local identity")
        self.write(destination / "shareable", "state.md", "Local state")

        loaded = self.loaded("egress")

        self.assertEqual(len(loaded), 4)
        self.assertIn("Local behavior", repr(loaded["behavior.md"]))
        self.assertNotIn("Builtin behavior", repr(loaded["behavior.md"]))
        self.assertIn("Local identity", repr(loaded["identity.md"]))
        self.assertNotIn("Builtin identity", repr(loaded["identity.md"]))
        self.assertIn("Builtin relationship", repr(loaded["relationship.md"]))
        self.assertIn("Local state", repr(loaded["state.md"]))

    def test_empty_local_template_does_not_erase_builtin_default(self):
        self.create_builtin()
        _assistant, destination = self.create_local()
        (destination / "shareable" / "behavior.md").write_text(
            entities.BEHAVIOR_TEMPLATE, encoding="utf-8"
        )

        loaded = self.loaded("egress")

        self.assertIn("Builtin behavior", repr(loaded["behavior.md"]))

    def test_egress_excludes_private_local_override_while_full_retains_it(self):
        self.create_builtin()
        _assistant, destination = self.create_local()
        self.write(destination / "local", "behavior.md", "Private behavior")

        egress = self.loaded("egress")
        full = self.loaded("full")

        self.assertIn("Builtin behavior", repr(egress["behavior.md"]))
        self.assertNotIn("Private behavior", repr(egress))
        self.assertIn("Private behavior", repr(full["behavior.md"]))

    def test_different_builtin_and_local_ids_fail_instead_of_merging(self):
        self.create_builtin("00000000-0000-4000-8000-000000000007")
        self.create_local()

        with self.assertRaisesRegex(
            entities.EntityContextError, "Built-in and local assistant contexts conflict"
        ):
            entities.load_assistant_documents("rot", view="egress")

    def test_prepared_ask_contains_one_effective_identity_and_behavior_document(self):
        self.create_builtin()
        assistant, destination = self.create_local()
        self.write(destination / "shareable", "identity.md", "Local identity")
        self.write(destination / "shareable", "behavior.md", "Local behavior")
        inspected = inspection.InspectedContext(
            "rot", assistant.id, None, None, None, None, None, None,
            Path("/work"),
            inspection.IdentificationSources(
                "local", "not configured", "not configured", "none"
            ),
            ()
        )
        request = invocation.AIRequest(
            "ask", "ask", "Question", inspected_context=inspected,
            agent_name="opencode"
        )

        with patch.object(
            invocation, "resolve_provider", return_value=(OPENCODE, None)
        ):
            plan = invocation.prepare(request)

        self.assertEqual(plan.provider_input.count("## Identity"), 1)
        self.assertEqual(plan.provider_input.count("## Behavior"), 1)
        self.assertIn("Local identity", plan.provider_input)
        self.assertIn("Local behavior", plan.provider_input)
        self.assertNotIn("Builtin identity", plan.provider_input)
        self.assertNotIn("Builtin behavior", plan.provider_input)


if __name__ == "__main__":
    unittest.main()
