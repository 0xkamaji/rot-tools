from pathlib import Path
import unittest
from unittest.mock import call, patch

from rotbot.contexts import entities, inspection, loader, machines, people, prompt
from rotbot.session.capabilities import CapabilityState


class PromptCompilerTests(unittest.TestCase):
    def block(self, context_type, name, *sections):
        return prompt.PromptContextBlock(
            context_type,
            f"{context_type}-id",
            name,
            tuple(sections)
        )

    def test_renders_semantic_blocks_and_separated_request(self):
        context = prompt.PromptContext(
            assistant=self.block(
                "assistant", "Rot", ("identity", "Rot identity marker")
            ),
            user=self.block(
                "user", "Kamaji", ("experience", "User experience marker")
            ),
            machine=self.block(
                "machine", "Laptop", ("portable facts", "CachyOS marker")
            ),
            project=self.block(
                "project",
                "rotbot",
                ("identity", "Project identity marker"),
                ("state", "Project state marker")
            ),
            working_directory="/work/rotbot",
            execution_backend="Codex"
        )

        rendered = prompt.build_ask_prompt(context, "What should I do next?")

        self.assertTrue(rendered.startswith("<rotbot_context_instructions>"))
        self.assertIn("<assistant_context>", rendered)
        self.assertIn("persistent assistant identity", rendered)
        self.assertIn("execution backend is not itself", rendered)
        self.assertIn("Rot identity marker", rendered)
        self.assertIn("<user_context>", rendered)
        self.assertIn("calibrate explanations", rendered)
        self.assertIn("User experience marker", rendered)
        self.assertIn("<machine_context>", rendered)
        self.assertIn("deterministic and general", rendered)
        self.assertIn("CachyOS marker", rendered)
        self.assertIn("<project_context>", rendered)
        self.assertIn("Project identity marker", rendered)
        self.assertIn("Project state marker", rendered)
        self.assertIn("Active project: rotbot", rendered)
        self.assertIn("Execution backend: Codex", rendered)
        self.assertIn(
            "<user_request>\n\nWhat should I do next?\n\n</user_request>",
            rendered
        )

    def test_missing_optional_context_omits_empty_blocks(self):
        context = prompt.PromptContext(
            assistant=None,
            user=None,
            machine=None,
            project=None,
            working_directory="/tmp",
            execution_backend="OpenCode"
        )

        rendered = prompt.build_ask_prompt(context, "Hello")

        for tag in ("assistant", "user", "machine", "project"):
            self.assertNotIn(f"<{tag}_context>", rendered)
        self.assertNotIn("/tmp", rendered)
        self.assertIn("Execution backend: OpenCode", rendered)
        self.assertIn("<user_request>", rendered)

    def test_context_refresh_reuses_semantic_context_without_local_activity(self):
        context = prompt.PromptContext(
            assistant=None,
            user=None,
            machine=None,
            project=self.block(
                "project", "signalrot", ("state", "Current project marker")
            ),
            working_directory="/work/signalrot",
            execution_backend="OpenCode"
        )

        rendered = prompt.build_context_refresh_prompt(context, "What changed?")

        self.assertIn("<rotbot_context_refresh_instructions>", rendered)
        self.assertIn("Current project marker", rendered)
        self.assertNotIn("/work/signalrot", rendered)
        self.assertIn("What changed?", rendered)
        self.assertNotIn("PRIVATE_HISTORY_MUST_NOT_ENTER_AI_PROMPT", rendered)
        self.assertNotIn("SHELL_OUTPUT_MUST_NOT_ENTER_AI_PROMPT", rendered)

    def test_runtime_capability_snapshot_is_fresh_and_separate(self):
        runtime = CapabilityState(
            assistant_id="assistant-id", mode="TALK", project_id="project-id",
            work_project_id=None, conversation=True, file_read=False,
            file_write=False, agent_execution=False, policy_valid=True,
            policy_fingerprint="fingerprint"
        )
        context = prompt.PromptContext(
            assistant=None, user=None, machine=None, project=None,
            working_directory="/work", execution_backend="OpenCode",
            runtime=runtime
        )

        rendered = prompt.build_ask_prompt(context, "Question")

        self.assertIn("Current mode: TALK", rendered)
        self.assertIn("AI file modification: unavailable", rendered)
        self.assertIn("Agent execution: unavailable", rendered)
        self.assertIn("<runtime_capability_state>", rendered)
        self.assertIn("Do not claim, promise, or imply", rendered)
        self.assertIn("must explicitly enter WORK mode", rendered)

    def test_resolver_loads_only_portable_machine_context(self):
        inspected = inspection.InspectedContext(
            None, None,
            None, None,
            "laptop", "machine-id",
            None, None,
            Path("/work"),
            inspection.IdentificationSources(
                "not configured", "not configured", "local config",
                "no matching project context"
            ),
            ()
        )
        portable_machine = machines.MachineContext(
            "laptop", "Laptop", {"operating_system": "PortableOS"}, "machine-id"
        )
        documents = (
            machines.MachineDocument(
                "metadata.toml",
                'type = "machine"\nname = "laptop"\n'
                'display_name = "Laptop"\noperating_system = "PortableOS"\n'
            ),
            machines.MachineDocument(
                "identity.md",
                "# Identity\n\n<!-- guidance -->\n\n## Purpose\n\nPortable purpose\n"
            ),
            machines.MachineDocument(
                "software.toml", '[[software]]\nname = "PortableTool"\n'
            )
        )

        with patch.object(
            machines, "load_machine_files", return_value=(portable_machine, documents)
        ) as load_portable, patch.object(
            machines,
            "load_local_machine_record",
            return_value={"connection": {"hostname": "PRIVATE_CONTEXT_MUST_NOT_LEAVE_ROTBOT"}}
        ) as load_private:
            context = prompt.resolve_prompt_context(inspected, "Codex")
            rendered = prompt.build_ask_prompt(context, "Question")
            private_context = prompt.resolve_prompt_context(
                inspected, "Codex", view="full"
            )
            private_rendered = prompt.build_ask_prompt(private_context, "Question")

        self.assertEqual(
            load_portable.call_args_list,
            [call("laptop", view="egress"), call("laptop", view="full")]
        )
        load_private.assert_not_called()
        self.assertNotIn("PortableOS", rendered)
        self.assertIn("PortableOS", private_rendered)
        self.assertIn("Portable purpose", rendered)
        self.assertNotIn("PortableTool", rendered)
        self.assertNotIn("PRIVATE_CONTEXT_MUST_NOT_LEAVE_ROTBOT", rendered)
        self.assertNotIn("<!--", rendered)

    def test_resolver_uses_dynamic_general_project_vision(self):
        inspected = inspection.InspectedContext(
            "rot", "assistant-id",
            "kamaji", "user-id",
            None, None,
            "rotbot", "project-id",
            Path("/work/rotbot"),
            inspection.IdentificationSources(
                "local config", "local config", "not configured", "source binding"
            ),
            ()
        )
        assistant = entities.AssistantContext("rot", "Rot", (), "assistant-id")
        user = entities.UserContext("kamaji", "Kamaji", (), "user-id")

        project = loader.Context(
            "rotbot", "Stable project identity", "Current project state", "project-id",
            knowledge=(
                loader.ProjectDocument("identity.md", "identity", "Stable project identity"),
                loader.ProjectDocument("state.md", "general", "Current project state"),
                loader.ProjectDocument("vision.md", "general", "Project vision marker"),
            )
        )
        with patch.object(
            entities,
            "load_assistant_documents",
            return_value=(assistant, (
                people.PersonDocument("identity.md", (("Traits", "Curious"),)),
                people.PersonDocument("behavior.md", (("Communication", "Direct"),))
            ))
        ), patch.object(
            entities,
            "load_user_documents",
            return_value=(user, (
                people.PersonDocument("experience.md", (("Technical", "Python"),)),
            ))
        ), patch.object(loader, "load_context", return_value=project) as load_project:
            context = prompt.resolve_prompt_context(inspected, "OpenCode")
            rendered = prompt.build_ask_prompt(context, "Question")

        load_project.assert_called_once_with("rotbot", view="egress")
        self.assertIn("Curious", rendered)
        self.assertIn("Direct", rendered)
        self.assertIn("Python", rendered)
        self.assertIn("Stable project identity", rendered)
        self.assertIn("Current project state", rendered)
        self.assertIn("Project vision marker", rendered)


if __name__ == "__main__":
    unittest.main()
