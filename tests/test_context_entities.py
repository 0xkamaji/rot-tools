from pathlib import Path
import shutil
import tempfile
import tomllib
import unittest
from unittest.mock import patch

from rotbot.agents import invocation
from rotbot.agents.config import OPENCODE
from rotbot.contexts import entities, inspection, learning, loader, modification


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
        structural = {"metadata.toml", "relationships.toml", "general", "private"}
        self.assertEqual({path.name for path in user_path.iterdir()}, structural)
        self.assertEqual(
            {path.name for path in assistant_path.iterdir()},
            structural | {"identity.md", "capabilities.toml"}
        )
        self.assertFalse((user_path / "identity.md").exists())
        self.assertTrue((user_path / "general" / "identity.md").is_file())
        self.assertTrue((user_path / "private" / "identity.md").is_file())
        self.assertTrue((assistant_path / "identity.md").is_file())
        self.assertTrue((user_path / "relationships.toml").is_file())
        self.assertTrue((assistant_path / "relationships.toml").is_file())
        self.assertEqual(
            tuple(path.name for path in user_path.joinpath("general").iterdir()),
            ("identity.md",)
        )
        self.assertEqual(
            tuple(path.name for path in user_path.joinpath("private").iterdir()),
            ("identity.md",)
        )
        self.assertEqual(tuple((assistant_path / "general").iterdir()), ())
        self.assertEqual(tuple((assistant_path / "private").iterdir()), ())
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

    def test_transitional_root_user_identity_moves_into_both_namespaces(self):
        user = entities.build_user_context(
            "legacy-user", context_id="00000000-0000-4000-8000-000000000009"
        )
        destination = entities.create_entity_context(user, root=self.root)
        (destination / "general" / "identity.md").unlink()
        (destination / "private" / "identity.md").write_text(
            "# Private Identity\n", encoding="utf-8"
        )
        (destination / "identity.md").write_text(
            "# Transitional Identity\n", encoding="utf-8"
        )

        self.assertEqual(entities.load_user_context(user.id, root=self.root), user)

        self.assertFalse((destination / "identity.md").exists())
        self.assertEqual(
            (destination / "general" / "identity.md").read_text(encoding="utf-8"),
            "# Transitional Identity\n"
        )
        self.assertEqual(
            (destination / "private" / "identity.md").read_text(encoding="utf-8"),
            "# Private Identity\n"
        )

    def test_entity_documents_union_identity_general_and_private_semantics(self):
        user = entities.build_user_context(
            "alex", context_id="00000000-0000-4000-8000-000000000005"
        )
        destination = entities.create_entity_context(user, root=self.root)
        (destination / "general" / "identity.md").write_text(
            "# Identity\n\n## Stable\n\nGeneral identity.\n", encoding="utf-8"
        )
        (destination / "private" / "identity.md").write_text(
            "# Identity\n\n## Private\n\nPrivate identity.\n", encoding="utf-8"
        )
        (destination / "general" / "experience.md").write_text(
            "# Experience\n\n## General\n\nGeneral experience.\n", encoding="utf-8"
        )
        (destination / "private" / "notes.md").write_text(
            "# Notes\n\n## Private\n\nPrivate note.\n", encoding="utf-8"
        )

        _user, documents = entities.load_user_documents(user.id, root=self.root)

        rendered = repr(documents)
        self.assertIn("General identity", rendered)
        self.assertIn("Private identity", rendered)
        self.assertIn("General experience", rendered)
        self.assertIn("Private note", rendered)


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
        (destination / "general").mkdir()
        (destination / "private").mkdir()
        (destination / "metadata.toml").write_text(
            entities.render_metadata(assistant), encoding="utf-8"
        )
        (destination / "capabilities.toml").write_text(
            entities.SAFE_CAPABILITIES, encoding="utf-8"
        )
        self.write(destination, "identity.md", "Builtin identity")
        (destination / "relationships.toml").write_text("", encoding="utf-8")
        self.write(destination / "general", "behavior.md", "Builtin behavior")
        self.write(destination / "general", "relationship.md", "Builtin relationship")
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
            tuple(loaded), ("identity.md", "behavior.md", "relationship.md")
        )
        self.assertIn("Builtin identity", repr(loaded["identity.md"]))

    def test_created_assistant_loads_dynamic_general_documents(self):
        _assistant, destination = self.create_local()
        self.write(destination / "general", "state.md", "Local state")

        loaded = self.loaded("egress")

        self.assertEqual(tuple(loaded), ("identity.md", "state.md"))
        self.assertIn("Local state", repr(loaded["state.md"]))

    def test_local_assistant_is_authoritative_without_builtin_fallback(self):
        self.create_builtin()
        _assistant, destination = self.create_local()
        self.write(destination / "general", "behavior.md", "Local behavior")
        self.write(destination, "identity.md", "Local identity")
        self.write(destination / "general", "state.md", "Local state")

        loaded = self.loaded("egress")

        self.assertEqual(len(loaded), 3)
        self.assertIn("Local behavior", repr(loaded["behavior.md"]))
        self.assertNotIn("Builtin behavior", repr(loaded["behavior.md"]))
        self.assertIn("Local identity", repr(loaded["identity.md"]))
        self.assertNotIn("Builtin identity", repr(loaded["identity.md"]))
        self.assertNotIn("relationship.md", loaded)
        self.assertIn("Local state", repr(loaded["state.md"]))

    def test_empty_general_document_does_not_resurrect_builtin_default(self):
        self.create_builtin()
        _assistant, destination = self.create_local()
        (destination / "general" / "behavior.md").write_text(
            "# Behavior\n\n<!-- Add general behavior knowledge here. -->\n",
            encoding="utf-8"
        )

        loaded = self.loaded("egress")

        self.assertNotIn("behavior.md", loaded)

    def test_egress_excludes_private_document_while_full_retains_it(self):
        self.create_builtin()
        _assistant, destination = self.create_local()
        self.write(destination / "private", "behavior.md", "Private behavior")

        egress = self.loaded("egress")
        full = self.loaded("full")

        self.assertNotIn("behavior.md", egress)
        self.assertNotIn("Private behavior", repr(egress))
        self.assertIn("Private behavior", repr(full["behavior.md"]))

    def test_local_loading_does_not_consult_conflicting_builtin(self):
        self.create_builtin("00000000-0000-4000-8000-000000000007")
        self.create_local()

        loaded = self.loaded("egress")
        self.assertEqual(tuple(loaded), ("identity.md",))

    def test_materialization_preserves_seed_and_is_idempotent(self):
        assistant = self.create_builtin()
        builtin = self.builtin_root / "rot"
        before = {
            path.relative_to(builtin): path.read_bytes()
            for path in builtin.rglob("*") if path.is_file()
        }

        materialized, destination = entities.materialize_builtin_assistant(assistant.id)

        self.assertEqual(materialized.id, assistant.id)
        self.assertEqual(
            tomllib.loads((destination / "metadata.toml").read_text())["id"],
            assistant.id
        )
        self.assertEqual(
            (destination / "capabilities.toml").read_text(),
            (builtin / "capabilities.toml").read_text()
        )
        self.assertEqual(
            (destination / "identity.md").read_text(),
            (builtin / "identity.md").read_text()
        )
        self.assertEqual(
            (destination / "general" / "behavior.md").read_text(),
            (builtin / "general" / "behavior.md").read_text()
        )
        self.assertEqual(tuple((destination / "private").iterdir()), ())
        self.assertEqual(before, {
            path.relative_to(builtin): path.read_bytes()
            for path in builtin.rglob("*") if path.is_file()
        })

        (destination / "identity.md").write_text(
            "# Identity\n\n## Details\n\nLocal evolution\n", encoding="utf-8"
        )
        self.write(builtin, "identity.md", "Updated builtin")
        second, same_destination = entities.materialize_builtin_assistant("rot")
        self.assertEqual(second.id, assistant.id)
        self.assertEqual(same_destination, destination)
        loaded = self.loaded("egress")
        self.assertIn("Local evolution", repr(loaded["identity.md"]))
        self.assertNotIn("Updated builtin", repr(loaded))

    def test_materialization_rejects_conflicting_local_id(self):
        self.create_builtin("00000000-0000-4000-8000-000000000007")
        self.create_local()
        with self.assertRaisesRegex(entities.EntityContextError, "conflicts"):
            entities.materialize_builtin_assistant("rot")

    def test_builtin_only_learning_materializes_before_writing(self):
        assistant = self.create_builtin()
        inspected = inspection.InspectedContext(
            "rot", assistant.id, None, None, None, None, None, None,
            Path("/work"),
            inspection.IdentificationSources(
                "local config", "not configured", "not configured", "none"
            ), ()
        )

        learned_assistant, path = learning.learn_text(
            "assistant", "Installation-specific knowledge", inspected=inspected
        )

        self.assertEqual(learned_assistant.id, assistant.id)
        self.assertEqual(
            path,
            self.context_root / "assistants" / "rot" / "private" / "learned.md"
        )
        self.assertIn("Installation-specific knowledge", path.read_text())

    def test_builtin_only_modification_materializes_before_editing(self):
        assistant = self.create_builtin()

        resolved, document = modification._entity_document(
            assistant.id, "behavior.md", "assistant", create=True
        )

        self.assertEqual(resolved.id, assistant.id)
        self.assertEqual(
            document,
            self.context_root / "assistants" / "rot" / "private" / "behavior.md"
        )

    def test_bootstrap_selection_and_existing_binding_materialize_builtin(self):
        assistant = self.create_builtin()
        with patch("builtins.input", return_value="1"), patch.object(
            inspection, "set_local_context_binding"
        ) as bind, patch.object(inspection, "rot_say"):
            selected = inspection._person_identity(
                {}, "assistant", "assistant", True, []
            )

        self.assertEqual(selected[:2], ("rot", assistant.id))
        bind.assert_called_once_with("assistant", assistant.id)
        destination = self.context_root / "assistants" / "rot"
        self.assertTrue(destination.is_dir())

        shutil.rmtree(destination)
        existing = inspection._person_identity(
            {"assistant": assistant.id}, "assistant", "assistant", True, []
        )
        self.assertEqual(existing[:2], ("rot", assistant.id))
        self.assertTrue(destination.is_dir())

    def test_prepared_ask_contains_one_effective_identity_and_behavior_document(self):
        self.create_builtin()
        assistant, destination = self.create_local()
        self.write(destination, "identity.md", "Local identity")
        self.write(destination / "general", "behavior.md", "Local behavior")
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
