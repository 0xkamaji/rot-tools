import os
from pathlib import Path
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

    def target_path(self, target):
        return {
            "user": self.root / "users" / "user",
            "assistant": self.root / "assistants" / "assistant",
            "project": self.project_path,
            "machine": self.machine_path,
            "contact": self.contact_path
        }[target] / "private" / "learned.md"

    def test_all_targets_append_exact_private_entries_without_general_writes(self):
        for target in learning.TARGETS:
            path = self.target_path(target)
            path.unlink(missing_ok=True)
            reference = "plop" if target == "contact" else None
            learning.learn_text(
                target, "First line\nsecond line", inspected=self.inspected,
                reference=reference
            )
            learning.learn_text(
                target, "Exact wording.", inspected=self.inspected,
                reference=reference
            )
            self.assertEqual(
                path.read_text(encoding="utf-8"),
                "# Learned\n\n- First line\n  second line\n\n- Exact wording.\n"
            )
            self.assertFalse((path.parent.parent / "general" / "learned.md").exists())
            if os.name != "nt":
                self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

        self.assertIn("Exact wording.", repr(
            entities.load_user_documents(self.user.id, root=self.root, view="full")[1]
        ))
        self.assertIn("Exact wording.", repr(
            entities.load_assistant_documents(
                self.assistant.id, root=self.root, view="full"
            )[1]
        ))
        self.assertIn("Exact wording.", loader.load_context("project", view="full").learned)
        self.assertIn("Exact wording.", repr(
            machines.load_machine_files("machine", view="full")[1]
        ))
        self.assertIn("Exact wording.", repr(
            people.load_person_documents("plop", view="full")[1]
        ))

    def test_invalid_entries_and_symlinks_are_rejected(self):
        for text in ("", "\0", "bad\x01control", "bad\x7fcontrol"):
            with self.assertRaises(learning.LearningError):
                learning.learn_text("project", text, inspected=self.inspected)
        with self.assertRaises(learning.LearningError):
            learning.learn_text(
                "project", "x" * (learning.MAX_LEARNED_ENTRY_BYTES + 1),
                inspected=self.inspected
            )
        path = self.target_path("project")
        path.unlink(missing_ok=True)
        path.symlink_to(self.project_path / "metadata.toml")
        with self.assertRaises(learning.LearningError):
            learning.learn_text("project", "safe", inspected=self.inspected)

    def test_atomic_failure_preserves_existing_document(self):
        path = self.target_path("project")
        path.write_text("# Learned\n\n- prior\n", encoding="utf-8")
        with patch.object(learning.os, "replace", side_effect=OSError("failed")):
            with self.assertRaises(learning.LearningError):
                learning.learn_text("project", "new", inspected=self.inspected)
        self.assertEqual(path.read_text(encoding="utf-8"), "# Learned\n\n- prior\n")
        self.assertEqual(tuple(path.parent.glob(".learned-*.tmp")), ())

    def test_edit_uses_shared_editor_and_validates_heading(self):
        path = self.target_path("project")
        path.write_text("# Learned\n\n- old\n", encoding="utf-8")
        with patch.object(
            learning, "edit_text", return_value="# Learned\n\n- edited\n"
        ) as editor:
            learning.edit_learned("project", inspected=self.inspected)
        editor.assert_called_once_with("# Learned\n\n- old\n")
        self.assertEqual(path.read_text(encoding="utf-8"), "# Learned\n\n- edited\n")

        with patch.object(learning, "edit_text", return_value="# Wrong\n"):
            with self.assertRaises(learning.LearningError):
                learning.edit_learned("project", inspected=self.inspected)
        self.assertEqual(path.read_text(encoding="utf-8"), "# Learned\n\n- edited\n")

        with patch.object(learning, "edit_text", side_effect=RuntimeError("editor failed")):
            with self.assertRaises(RuntimeError):
                learning.edit_learned("project", inspected=self.inspected)
        self.assertEqual(path.read_text(encoding="utf-8"), "# Learned\n\n- edited\n")

    def test_show_prints_exact_document_without_modifying_it(self):
        path = self.target_path("user")
        expected = "# Learned\n\n- exact shown text\n"
        path.write_text(expected, encoding="utf-8")
        before = path.stat()
        args = SimpleNamespace(
            learn_action="show", learn_target="user",
            inspected_context=self.inspected
        )

        with patch("builtins.print") as output:
            result = learning.learn_command(args)

        self.assertEqual(result, 0)
        output.assert_called_once_with(expected, end="")
        self.assertEqual(path.read_text(encoding="utf-8"), expected)
        self.assertEqual(path.stat().st_mtime_ns, before.st_mtime_ns)

    def test_show_missing_document_fails_without_creating_it(self):
        path = self.target_path("project")
        path.unlink(missing_ok=True)
        args = SimpleNamespace(
            learn_action="show", learn_target="project",
            inspected_context=self.inspected
        )

        with patch.object(learning, "rot_say") as say:
            result = learning.learn_command(args)

        self.assertEqual(result, 2)
        self.assertIn("No learned knowledge", say.call_args.args[0])
        self.assertFalse(path.exists())

    def test_unresolved_target_offers_existing_context(self):
        unresolved = self.inspected._replace(project=None, project_id=None)
        with patch("builtins.input", return_value="1"), patch.object(learning, "rot_say"):
            context, path = learning.learn_text("project", "selected", inspected=unresolved)
        self.assertEqual(context.name, "project")
        self.assertEqual(path, self.target_path("project"))

    def test_omitted_text_prompts_after_confirming_resolved_target(self):
        args = SimpleNamespace(
            learn_action="append", learn_target="user", text=[],
            inspected_context=self.inspected
        )
        with patch("builtins.input", return_value="Prompted exact wording") as prompt, patch.object(
            learning, "rot_say"
        ) as say:
            result = learning.learn_command(args)

        self.assertEqual(result, 0)
        prompt.assert_called_once_with("> ")
        self.assertIn("Learning target: user 'user'", say.call_args_list[0].args[0])
        self.assertIn(
            "- Prompted exact wording",
            self.target_path("user").read_text(encoding="utf-8")
        )

    def test_omitted_text_eof_or_empty_input_does_not_modify_context(self):
        path = self.target_path("assistant")
        path.write_text("# Learned\n\n- existing\n", encoding="utf-8")
        before = path.read_text(encoding="utf-8")
        args = SimpleNamespace(
            learn_action="append", learn_target="assistant", text=[],
            inspected_context=self.inspected
        )
        for response in (EOFError(), ""):
            effect = response if isinstance(response, BaseException) else None
            with patch(
                "builtins.input", side_effect=effect, return_value=response
            ), patch.object(learning, "rot_say"):
                self.assertEqual(learning.learn_command(args), 2)
            self.assertEqual(path.read_text(encoding="utf-8"), before)

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
        (destination / "identity.md").write_text(
            "# Identity\n\n## Public\n\nGENERAL IDENTITY FACT 67890\n", encoding="utf-8"
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
