from pathlib import Path
import tempfile
import unittest

from rotbot.contexts import entities
from rotbot.session import capabilities


class CapabilityTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name) / "context"
        (self.root / "assistants").mkdir(parents=True)

    def tearDown(self):
        self.temporary_directory.cleanup()

    def assistant(self):
        assistant = entities.build_assistant_context(
            "forge", context_id="00000000-0000-4000-8000-000000000007"
        )
        entities.create_entity_context(assistant, root=self.root)
        return assistant

    def test_safe_new_assistant_defaults_to_talk_without_work(self):
        assistant = self.assistant()
        policy = capabilities.load_assistant_policy(assistant.id, root=self.root)
        state = capabilities.resolve_capability_state(
            assistant.id, policy, "WORK", "project-id", "project-id"
        )

        self.assertTrue(policy.valid)
        self.assertEqual(policy.default_mode, "TALK")
        self.assertFalse(policy.work_enabled)
        self.assertEqual(state.mode, "TALK")
        self.assertFalse(state.file_write)

    def test_valid_policy_can_enable_only_core_scoped_work(self):
        assistant = self.assistant()
        path = entities.entity_directory(assistant, self.root) / "capabilities.toml"
        path.write_text(entities.SAFE_CAPABILITIES.replace("enabled = false", "enabled = true"), encoding="utf-8")
        policy = capabilities.load_assistant_policy(assistant.id, root=self.root)

        wrong_scope = capabilities.resolve_capability_state(
            assistant.id, policy, "WORK", "project-b", "project-a"
        )
        valid = capabilities.resolve_capability_state(
            assistant.id, policy, "WORK", "project-a", "project-a"
        )

        self.assertEqual(wrong_scope.mode, "TALK")
        self.assertEqual(valid.mode, "WORK")
        self.assertTrue(valid.file_read and valid.file_write and valid.agent_execution)

    def test_invalid_policy_and_unknown_mode_fail_closed(self):
        assistant = self.assistant()
        path = entities.entity_directory(assistant, self.root) / "capabilities.toml"
        path.write_text("not = [valid", encoding="utf-8")
        policy = capabilities.load_assistant_policy(assistant.id, root=self.root)
        state = capabilities.resolve_capability_state(
            assistant.id, policy, "ADMIN", "project-id", "project-id"
        )

        self.assertFalse(policy.valid)
        self.assertEqual(state.mode, "TALK")
        self.assertFalse(state.file_read or state.file_write or state.agent_execution)

    def test_policy_cannot_disable_safe_start_or_project_revocation(self):
        assistant = self.assistant()
        path = entities.entity_directory(assistant, self.root) / "capabilities.toml"
        path.write_text(
            entities.SAFE_CAPABILITIES
            .replace('default_mode = "talk"', 'default_mode = "work"')
            .replace(
                "revoke_work_on_project_change = true",
                "revoke_work_on_project_change = false"
            ),
            encoding="utf-8"
        )

        policy = capabilities.load_assistant_policy(assistant.id, root=self.root)

        self.assertFalse(policy.valid)
        self.assertEqual(policy.default_mode, "TALK")


if __name__ == "__main__":
    unittest.main()
