import os
from pathlib import Path
import shutil
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from rotbot.agents import invocation
from rotbot.agents.config import OPENCODE
from rotbot.contexts import entities, inspection, learning, loader, machines, people


class LearningTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "contexts"
        self.root.mkdir()
        self.loader_patch = patch.object(loader, "CONTEXT_ROOT", self.root)
        self.loader_patch.start()

        self.user = entities.build_user_context("user")
        entities.create_entity_context(self.user, root=self.root)
        self.assistant = entities.build_assistant_context("assistant")
        entities.create_entity_context(self.assistant, root=self.root)
        (self.root / "machines").mkdir()
        self.machine_path = machines.create_machine(
            "machine", machines_root=self.root / "machines"
        )
        self.contact = people.build_person_context("plop", "contact")
        self.contact_path = people.create_person_context("plop", "contact")
        self.project_path = self.root / "projects" / "project"
        (self.project_path / "general").mkdir(parents=True, mode=0o700)
        (self.project_path / "private").mkdir(mode=0o700)
        (self.project_path / "metadata.toml").write_text(
            loader.render_project_metadata("project"), encoding="utf-8"
        )
        (self.project_path / "identity.md").write_text("# Project\n", encoding="utf-8")
        (self.project_path / "relationships.toml").write_text("", encoding="utf-8")
        self.inspected = inspection.InspectedContext(
            "assistant", self.assistant.id, "user", self.user.id,
            "machine", machines.load_machine_context("machine").id,
            "project", loader.load_context("project").id,
            Path("/work"),
            inspection.IdentificationSources(
                "local config", "local config", "local config", "source binding"
            ), ()
        )

    def tearDown(self):
        self.loader_patch.stop()
        self.temporary.cleanup()

    def target_directory(self, target):
        return {
            "user": self.root / "users" / "user",
            "assistant": self.root / "assistants" / "assistant",
            "project": self.project_path,
            "machine": self.machine_path,
            "contact": self.contact_path
        }[target]

    def knowledge_path(self, target, namespace="private", category="learned"):
        return self.target_directory(target) / namespace / f"{category}.md"

    def category_choice(self, target, namespace, category):
        files = sorted(
            (self.target_directory(target) / namespace).glob("*.md"),
            key=lambda path: path.name
        )
        return str([path.stem for path in files].index(category) + 1)

    def args(self, action, target="project", text=None):
        return SimpleNamespace(
            learn_action=action, learn_target=target,
            inspected_context=self.inspected, text=[] if text is None else text
        )

    def test_all_five_targets_learn_to_general_and_private(self):
        for target in learning.TARGETS:
            reference = "plop" if target == "contact" else None
            for namespace in learning.DISCLOSURES:
                path = self.knowledge_path(target, namespace, "observations")
                if os.name != "nt":
                    os.chmod(path.parent, 0o755)
                learning.learn_text(
                    target, f"{target} {namespace}\nsecond line",
                    inspected=self.inspected, reference=reference,
                    namespace=namespace, category="observations"
                )
                learning.learn_text(
                    target, "Exact wording.", inspected=self.inspected,
                    reference=reference, namespace=namespace,
                    category="observations"
                )
                self.assertEqual(
                    path.read_text(encoding="utf-8"),
                    f"# Observations\n\n- {target} {namespace}\n"
                    "  second line\n\n- Exact wording.\n"
                )
                if os.name != "nt":
                    self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)
                    self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_cli_new_category_uses_selected_disclosure_path_and_heading(self):
        new_choice = str(len(tuple((self.project_path / "general").glob("*.md"))) + 1)
        with patch(
            "builtins.input", side_effect=["1", new_choice, "release_notes"]
        ), patch.object(learning, "rot_say"):
            result = learning.learn_command(
                self.args("append", text=["A", "new", "fact"])
            )
        path = self.knowledge_path("project", "general", "release_notes")
        self.assertEqual(result, 0)
        self.assertEqual(path.read_text(encoding="utf-8"), "# Release Notes\n\n- A new fact\n")
        self.assertFalse(self.knowledge_path("project", "private", "release_notes").exists())

    def test_cli_append_existing_never_confuses_namespaces(self):
        general = self.knowledge_path("project", "general", "notes")
        private = self.knowledge_path("project", "private", "notes")
        general.write_text("# Notes\n\n- public prior\n", encoding="utf-8")
        private.write_text("# Notes\n\n- private prior\n", encoding="utf-8")
        with patch(
            "builtins.input",
            side_effect=["2", self.category_choice("project", "private", "notes")]
        ), patch.object(learning, "rot_say"):
            result = learning.learn_command(
                self.args("append", text=["private", "addition"])
            )
        self.assertEqual(result, 0)
        self.assertEqual(general.read_text(encoding="utf-8"), "# Notes\n\n- public prior\n")
        self.assertEqual(
            private.read_text(encoding="utf-8"),
            "# Notes\n\n- private prior\n\n- private addition\n"
        )

    def test_invalid_entries_are_rejected(self):
        for text in ("", "\0", "bad\x01control", "bad\x7fcontrol"):
            with self.assertRaises(learning.LearningError):
                learning.learn_text("project", text, inspected=self.inspected)
        with self.assertRaises(learning.LearningError):
            learning.learn_text(
                "project", "x" * (learning.MAX_LEARNED_ENTRY_BYTES + 1),
                inspected=self.inspected
            )

    def test_invalid_namespaces_categories_and_cli_traversal_are_rejected(self):
        with self.assertRaises(learning.LearningError):
            learning.learn_text(
                "project", "safe", inspected=self.inspected, namespace="local"
            )
        categories = (
            "", "..", "../escape", "nested/name", "nested\\name", ".hidden",
            "bad\x7f"
        )
        for category in categories:
            with self.subTest(category=category), self.assertRaises(learning.LearningError):
                learning.learn_text(
                    "project", "safe", inspected=self.inspected,
                    namespace="general", category=category
                )
        outside = self.project_path / "escape.md"
        new_choice = str(len(tuple((self.project_path / "general").glob("*.md"))) + 1)
        with patch(
            "builtins.input", side_effect=["1", new_choice, "../escape"]
        ), patch.object(learning, "rot_say"):
            self.assertEqual(
                learning.learn_command(self.args("append", text=["unsafe"])), 2
            )
        self.assertFalse(outside.exists())

    def test_symlink_protection_does_not_touch_target(self):
        path = self.knowledge_path("project")
        path.unlink(missing_ok=True)
        target = self.project_path / "metadata.toml"
        before = target.read_text(encoding="utf-8")
        path.symlink_to(target)
        with self.assertRaises(learning.LearningError):
            learning.learn_text("project", "safe", inspected=self.inspected)
        self.assertEqual(target.read_text(encoding="utf-8"), before)

    def test_atomic_failure_preserves_existing_document(self):
        path = self.knowledge_path("project")
        path.write_text("# Learned\n\n- prior\n", encoding="utf-8")
        with patch.object(learning.os, "replace", side_effect=OSError("failed")):
            with self.assertRaises(learning.LearningError):
                learning.learn_text("project", "new", inspected=self.inspected)
        self.assertEqual(path.read_text(encoding="utf-8"), "# Learned\n\n- prior\n")
        self.assertEqual(tuple(path.parent.glob(".learned-*.tmp")), ())

    def test_cli_edit_menu_uses_editor_for_selected_category(self):
        path = self.knowledge_path("project", "general", "notes")
        path.write_text("# Notes\n\n- old\n", encoding="utf-8")
        with patch.object(
            learning, "edit_text", return_value="# Notes\n\n- edited\n"
        ) as editor, patch(
            "builtins.input",
            side_effect=["1", self.category_choice("project", "general", "notes")]
        ), patch.object(learning, "rot_say"):
            result = learning.learn_command(self.args("edit"))
        self.assertEqual(result, 0)
        editor.assert_called_once_with("# Notes\n\n- old\n")
        self.assertEqual(path.read_text(encoding="utf-8"), "# Notes\n\n- edited\n")

    def test_cli_show_menu_has_no_mutation(self):
        path = self.knowledge_path("user", "general", "profile")
        expected = "# Profile\n\n- exact shown text\n"
        path.write_text(expected, encoding="utf-8")
        before = path.stat()
        with patch(
            "builtins.input",
            side_effect=["1", self.category_choice("user", "general", "profile")]
        ), patch("builtins.print") as output, patch.object(learning, "rot_say"):
            result = learning.learn_command(self.args("show", "user"))
        self.assertEqual(result, 0)
        output.assert_called_once_with(expected, end="")
        self.assertEqual(path.read_text(encoding="utf-8"), expected)
        self.assertEqual(path.stat().st_mtime_ns, before.st_mtime_ns)

    def test_back_one_level_does_not_resolve_target_again(self):
        path = self.knowledge_path("project", "private", "notes")
        path.write_text("# Notes\n\n- private\n", encoding="utf-8")
        general_back = str(len(tuple((self.project_path / "general").glob("*.md"))) + 1)
        with patch.object(
            learning, "_resolve", wraps=learning._resolve
        ) as resolve, patch(
            "builtins.input", side_effect=[
                "1", general_back, "2",
                self.category_choice("project", "private", "notes")
            ]
        ), patch("builtins.print"), patch.object(learning, "rot_say"):
            result = learning.learn_command(self.args("show"))
        self.assertEqual(result, 0)
        self.assertEqual(resolve.call_count, 1)

    def test_exit_from_disclosure_or_category_changes_nothing(self):
        path = self.knowledge_path("project", "general", "notes")
        path.write_text("# Notes\n\n- unchanged\n", encoding="utf-8")
        before = {
            item.relative_to(self.project_path): item.read_bytes()
            for item in self.project_path.rglob("*") if item.is_file()
        }
        category_exit = str(len(tuple((self.project_path / "general").glob("*.md"))) + 3)
        for responses in (["3"], ["1", category_exit]):
            with patch("builtins.input", side_effect=responses), patch.object(
                learning, "rot_say"
            ):
                self.assertEqual(
                    learning.learn_command(self.args("append", text=["not", "stored"])), 0
                )
            after = {
                item.relative_to(self.project_path): item.read_bytes()
                for item in self.project_path.rglob("*") if item.is_file()
            }
            self.assertEqual(after, before)

    def test_exit_does_not_materialize_builtin_assistant(self):
        local = self.root / "assistants" / "assistant"
        builtin_root = Path(self.temporary.name) / "builtin" / "assistants"
        builtin_root.mkdir(parents=True)
        shutil.copytree(local, builtin_root / "assistant")
        shutil.rmtree(local)

        with patch.object(
            entities, "builtin_assistants_root", return_value=builtin_root
        ), patch("builtins.input", return_value="3"), patch.object(
            learning, "rot_say"
        ):
            result = learning.learn_command(self.args("append", "assistant", ["unused"]))

        self.assertEqual(result, 0)
        self.assertFalse(local.exists())

    def test_unresolved_target_offers_existing_context(self):
        unresolved = self.inspected._replace(project=None, project_id=None)
        with patch("builtins.input", return_value="1"), patch.object(learning, "rot_say"):
            context, path = learning.learn_text("project", "selected", inspected=unresolved)
        self.assertEqual(context.name, "project")
        self.assertEqual(path, self.knowledge_path("project"))

    def test_omitted_text_eof_or_empty_does_not_modify_context(self):
        path = self.knowledge_path("assistant")
        path.write_text("# Learned\n\n- existing\n", encoding="utf-8")
        before = path.read_text(encoding="utf-8")
        for response in (EOFError(), ""):
            responses = [
                "2", self.category_choice("assistant", "private", "learned"), response
            ]
            with patch("builtins.input", side_effect=responses), patch.object(
                learning, "rot_say"
            ):
                self.assertEqual(
                    learning.learn_command(self.args("append", "assistant")), 2
                )
            self.assertEqual(path.read_text(encoding="utf-8"), before)

    def test_private_learned_legacy_helpers_still_work(self):
        path = self.knowledge_path("project")
        path.unlink(missing_ok=True)
        context, learned_path = learning.learn_text(
            "project", "legacy", inspected=self.inspected
        )
        shown_context, shown_path, content = learning.show_learned(
            "project", inspected=self.inspected
        )
        edited_context, edited_path = learning.edit_learned(
            "project", inspected=self.inspected,
            editor=lambda original: original.replace("legacy", "edited legacy")
        )
        self.assertEqual(
            (context.name, shown_context.name, edited_context.name),
            ("project", "project", "project")
        )
        self.assertEqual((learned_path, shown_path, edited_path), (path, path, path))
        self.assertIn("- legacy", content)
        self.assertIn("- edited legacy", path.read_text(encoding="utf-8"))

    def test_learning_has_no_provider_dependency(self):
        with patch.object(invocation, "invoke") as invoke, patch.object(
            invocation, "execute"
        ) as execute, patch.object(invocation, "start_provider_process") as start:
            learning.learn_text("project", "deterministic", inspected=self.inspected)
        invoke.assert_not_called()
        execute.assert_not_called()
        start.assert_not_called()


class TrustPreparationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.contexts = self.base / "data" / "rotbot" / "contexts"
        self.contexts.mkdir(parents=True)
        self.loader_patch = patch.object(loader, "CONTEXT_ROOT", self.contexts)
        self.loader_patch.start()
        self.user = entities.build_user_context("kamaji", "Kamaji")
        destination = entities.create_entity_context(self.user, root=self.contexts)
        (destination / "private" / "learned.md").write_text(
            "# Learned\n\n- LOCAL SECRET TEST FACT 12345\n", encoding="utf-8"
        )
        (destination / "general" / "identity.md").write_text(
            "# Identity\n\n## Public\n\nGENERAL IDENTITY FACT 67890\n", encoding="utf-8"
        )
        learning.learn_text(
            "user", "GENERAL LEARNED FACT 24680", inspected=inspection.InspectedContext(
                None, None, self.user.name, self.user.id, None, None, None, None,
                Path("/work"),
                inspection.IdentificationSources(
                    "not configured", "local config", "not configured", "none"
                ), ()
            ), namespace="general", category="preferences"
        )
        self.inspected = inspection.InspectedContext(
            None, None, self.user.name, self.user.id, None, None, None, None,
            Path("/work"),
            inspection.IdentificationSources(
                "not configured", "local config", "not configured", "none"
            ), ()
        )
        self.environment = {
            "XDG_CONFIG_HOME": str(self.base / "config"),
            "XDG_DATA_HOME": str(self.base / "data")
        }

    def tearDown(self):
        self.loader_patch.stop()
        self.temporary.cleanup()

    def prepare(self, trust=None):
        config = self.base / "config" / "rotbot" / "config.toml"
        if trust is not None:
            config.parent.mkdir(parents=True, exist_ok=True)
            config.write_text(
                f'[ai.agents.opencode]\ntrust = "{trust}"\n', encoding="utf-8"
            )
        with patch.dict(os.environ, self.environment, clear=True), patch.object(
            invocation, "resolve_provider", return_value=(OPENCODE, None)
        ):
            return invocation.prepare(invocation.AIRequest(
                "ask", "ask", "question", agent_name="opencode",
                inspected_context=self.inspected
            ))

    def test_trusted_private_and_external_apply_at_final_provider_input(self):
        private = self.prepare("trusted_private")
        self.assertEqual((private.trust_level, private.context_view), ("trusted_private", "full"))
        self.assertIn("LOCAL SECRET TEST FACT 12345", private.provider_input)
        self.assertIn("GENERAL IDENTITY FACT 67890", private.provider_input)

        external = self.prepare("external")
        self.assertEqual((external.trust_level, external.context_view), ("external", "egress"))
        self.assertNotIn("LOCAL SECRET TEST FACT 12345", external.provider_input)
        self.assertIn("GENERAL IDENTITY FACT 67890", external.provider_input)
        self.assertIn("GENERAL LEARNED FACT 24680", external.provider_input)
        self.assertNotEqual(private.context_fingerprint, external.context_fingerprint)

    def test_unconfigured_opencode_and_qwen_named_request_fail_closed(self):
        external = self.prepare()
        self.assertEqual((external.trust_level, external.context_view), ("external", "egress"))
        self.assertNotIn("LOCAL SECRET TEST FACT 12345", external.provider_input)
        with patch.dict(os.environ, self.environment, clear=True), patch.object(
            invocation, "resolve_provider", return_value=(OPENCODE, None)
        ):
            plan = invocation.prepare(invocation.AIRequest(
                "ask", "ask", "qwen", agent_name="opencode",
                inspected_context=self.inspected
            ))
        self.assertEqual(plan.trust_level, "external")

    def test_external_resolution_never_constructs_full_context(self):
        with patch(
            "rotbot.contexts.prompt.entities.load_user_documents",
            wraps=entities.load_user_documents
        ) as load:
            self.prepare("external")
        self.assertEqual(load.call_args.kwargs["view"], "egress")
