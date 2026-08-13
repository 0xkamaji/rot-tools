import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import ANY, Mock, patch

from rotbot.agents.invocation import AIInvocationResult
from rotbot.commands.machine import MachineInspection
from rotbot.contexts import creation as context_creation
from rotbot.contexts import loader as contexts
from rotbot.contexts import matching as context_matching
from rotbot.contexts.config import ConfigError, config_path, get_context_binding


class ContextCreationTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.context_root = self.root / "contexts"
        self.context_root.mkdir()
        self.project_context_root = self.context_root / "projects"
        self.project_context_root.mkdir()
        self.project = self.root / "project"
        self.project.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=self.project, check=True)
        subprocess.run(
            [
                "git", "remote", "add", "upstream",
                "git@github.com:example/project.git"
            ],
            cwd=self.project,
            check=True
        )
        (self.project / "README.md").write_text("# Example\nSafe docs.\n", encoding="utf-8")
        (self.project / "main.py").write_text("def main():\n    return 0\n", encoding="utf-8")
        (self.project / "src").mkdir()

        self.context_patch = patch.object(contexts, "CONTEXT_ROOT", self.context_root)
        self.context_patch.start()
        self.environment = patch.dict(
            os.environ,
            {"XDG_CONFIG_HOME": str(self.root / "config-home")},
            clear=True
        )
        self.environment.start()
        self.agent_output = json.dumps({
            "identity": (
                "# Example Identity\n\nExample is a small test project. Its identity "
                "is human-maintained and must not be rewritten automatically."
            ),
            "state": (
                "# Example Current State\n\nThe project currently has a Python entry point "
                "and source directory."
            )
        })

    def tearDown(self):
        self.environment.stop()
        self.context_patch.stop()
        self.temporary_directory.cleanup()

    def args(self, name="example", path=None, agent=None):
        return argparse.Namespace(
            name=name,
            path=str(self.project if path is None else path),
            agent=agent
        )

    def run_add(self, answer="n", output=None, returncode=0, args=None):
        output = self.agent_output if output is None else output
        args = self.args() if args is None else args
        def invoke(invocation, validator=None, **_kwargs):
            if returncode != 0:
                return AIInvocationResult(
                    invocation, returncode, output, 0.1, "Test",
                    validation_error=f"Test failed with exit code {returncode}."
                )
            try:
                value = validator(output) if validator is not None else None
            except Exception as error:
                return AIInvocationResult(
                    invocation, 0, output, 0.1, "Test",
                    validation_error=str(error), attempts=invocation.retries + 1
                )
            return AIInvocationResult(invocation, 0, output, 0.1, "Test", value=value)
        with patch.object(
            context_creation,
            "invoke",
            side_effect=invoke
        ) as invocation, patch("builtins.input", return_value=answer), patch.object(
            context_creation,
            "rot_say"
        ) as rot_say, patch.object(context_creation, "rot_continue") as rot_continue:
            result = context_creation._add_project_context(args)
        return result, invocation, rot_say, rot_continue

    def test_invalid_names_and_existing_context_are_rejected_before_agent(self):
        for name in ("", "../outside", "nested/name", ".hidden"):
            with self.subTest(name=name):
                result, stream_agent, _rot_say, _rot_continue = self.run_add(
                    args=self.args(name=name)
                )
                self.assertEqual(result, 1)
                stream_agent.assert_not_called()

        existing = self.project_context_root / "existing"
        existing.mkdir()
        (existing / "identity.md").write_text("unchanged", encoding="utf-8")
        result, stream_agent, rot_say, _rot_continue = self.run_add(
            args=self.args(name="existing")
        )
        self.assertEqual(result, 1)
        stream_agent.assert_not_called()
        self.assertIn("already exists", rot_say.call_args.args[0])
        self.assertEqual(
            (existing / "identity.md").read_text(encoding="utf-8"),
            "unchanged"
        )

    def test_invalid_project_paths_are_rejected_before_agent(self):
        ordinary_file = self.root / "file.txt"
        ordinary_file.write_text("file", encoding="utf-8")
        for path in (
            self.root / "missing",
            ordinary_file,
            self.context_root,
            self.project_context_root
        ):
            with self.subTest(path=path):
                result, stream_agent, _rot_say, _rot_continue = self.run_add(
                    args=self.args(path=path)
                )
                self.assertEqual(result, 1)
                stream_agent.assert_not_called()

    def test_bounded_inspection_excludes_dependencies_secrets_and_binaries(self):
        (self.project / "node_modules").mkdir()
        (self.project / ".cache").mkdir()
        (self.project / ".env").write_text("TOKEN=do-not-send", encoding="utf-8")
        (self.project / "private.key").write_text("private", encoding="utf-8")
        (self.project / "image.png").write_bytes(b"\x89PNG\0secret")

        synopsis, required, optional = context_creation._inspect_project(
            self.project,
            ("github.com/example/project",)
        )

        self.assertNotIn("node_modules", synopsis)
        self.assertNotIn(".cache", synopsis)
        self.assertNotIn(".env", synopsis)
        self.assertNotIn("do-not-send", synopsis)
        self.assertNotIn("private.key", synopsis)
        self.assertNotIn("image.png", synopsis)
        self.assertLessEqual(len(synopsis.encode("utf-8")), 64_000)
        self.assertEqual(required[:2], ("main.py", "src/"))
        self.assertNotIn(str(self.project), required + optional)

    def test_bounded_inspection_skips_secrets_inside_approved_files(self):
        secret = "client_secret = super-sensitive-value"
        (self.project / "main.py").write_text(secret, encoding="utf-8")

        synopsis, _required, _optional = context_creation._inspect_project(
            self.project,
            ("github.com/example/project",)
        )

        self.assertIn("main.py", synopsis)
        self.assertNotIn(secret, synopsis)
        self.assertNotIn("super-sensitive-value", synopsis)

    def test_prompt_contains_only_approved_relative_evidence(self):
        secret_config = self.root / "config-home" / "rot" / "config.toml"
        secret_config.parent.mkdir(parents=True)
        secret_config.write_text('private = "do-not-send"\n', encoding="utf-8")

        result, stream_agent, _rot_say, _rot_continue = self.run_add(answer="n")

        self.assertEqual(result, 0)
        stream_agent.assert_not_called()
        synopsis, _required, _optional = context_creation._inspect_project(
            self.project, ("github.com/example/project",)
        )
        prompt = context_creation._agent_prompt("example", synopsis)
        self.assertIn("github.com/example/project", prompt)
        self.assertIn("main.py", prompt)
        self.assertIn("exactly two string keys", prompt)
        self.assertIn("do not draft match.toml or vision.md", prompt)
        self.assertNotIn(str(self.project), prompt)
        self.assertNotIn(str(secret_config), prompt)
        self.assertNotIn("do-not-send", prompt)

    def test_agent_failure_or_invalid_output_preserves_usable_context(self):
        cases = (
            (7, "failure"),
            (0, ""),
            (0, "not json"),
            (0, json.dumps({"identity": "# Identity"})),
            (0, json.dumps({
                "identity": "# Identity",
                "state": "# State",
                "match": "# Match"
            }))
        )
        for index, (returncode, output) in enumerate(cases):
            with self.subTest(returncode=returncode, output=output):
                name = f"example-{index}"
                result, _agent, _rot_say, _rot_continue = self.run_add(
                    answer="yes",
                    output=output,
                    returncode=returncode,
                    args=self.args(name=name)
                )
                self.assertEqual(result, 0)
                destination = self.project_context_root / name
                self.assertTrue(destination.exists())
                self.assertEqual(
                    (destination / "local" / "identity.md").read_text(encoding="utf-8"),
                    context_creation._placeholder_documents(
                        name, context_matching.load_match_definition(name)
                    )["identity"]
                )
                self.assertEqual(
                    get_context_binding(name)["source_path"], str(self.project)
                )

    def test_agent_output_rejects_control_characters_and_absolute_paths(self):
        outputs = (
            json.dumps({
                "identity": (
                    "# Identity\n\nHuman-maintained and must not be rewritten "
                    "automatically.\n\x1b[2J"
                ),
                "state": "# State\n\nCurrent."
            }),
            json.dumps({
                "identity": (
                    "# Identity\n\nHuman-maintained and must not be rewritten "
                    "automatically."
                ),
                "state": "# State\n\nStored at /home/someone/project/main.py."
            })
        )
        for index, output in enumerate(outputs):
            with self.subTest(output=output):
                name = f"unsafe-{index}"
                result, _agent, _rot_say, _rot_continue = self.run_add(
                    answer="yes",
                    output=output,
                    args=self.args(name=name)
                )
                self.assertEqual(result, 0)
                identity = (
                    self.project_context_root / name / "local" / "identity.md"
                ).read_text(encoding="utf-8")
                self.assertIn(context_creation.PLACEHOLDER_POINT, identity)

    def test_identity_does_not_require_literal_maintenance_wording(self):
        output = json.dumps({
            "identity": "# Identity\n\nA stable description of the project.",
            "state": "# State\n\nThe project currently has an entry point."
        })

        result, _agent, _rot_say, _rot_continue = self.run_add(
            answer="no", output=output
        )

        self.assertEqual(result, 0)

    def test_context_enrichment_requests_one_runtime_retry(self):
        result, invocation, _rot_say, _rot_continue = self.run_add(answer="yes")

        self.assertEqual(result, 0)
        request = invocation.call_args.args[0]
        self.assertEqual(request.purpose, "context_development")
        self.assertEqual(request.parent_command, "context add")
        self.assertEqual(request.retries, 1)
        self.assertFalse(request.conversation)

    def test_context_is_created_bound_and_loadable_before_enrichment(self):
        destination = self.project_context_root / "example"

        def verify_created(invocation, **_kwargs):
            self.assertTrue((destination / "local" / "identity.md").is_file())
            self.assertTrue((destination / "local" / "state.md").is_file())
            self.assertTrue((destination / "local" / "vision.md").is_file())
            self.assertTrue((destination / "local" / "match.toml").is_file())
            self.assertEqual(contexts.load_context("example").name, "example")
            self.assertEqual(get_context_binding("example")["source_path"], str(self.project))
            return AIInvocationResult(
                invocation, 127, "", 0.1, "Test", validation_error="unavailable"
            )

        with patch.object(
            context_creation, "invoke", side_effect=verify_created
        ) as invocation, patch("builtins.input", return_value="yes"), patch.object(
            context_creation, "rot_say"
        ), patch.object(context_creation, "rot_continue"):
            result = context_creation._add_project_context(self.args())

        self.assertEqual(result, 0)
        invocation.assert_called_once()

    def test_context_develop_replaces_only_placeholders(self):
        self.run_add(answer="yes", output="not json")

        with patch.object(
            context_creation,
            "invoke",
            side_effect=lambda invocation, validator, **_kwargs: AIInvocationResult(
                invocation, 0, self.agent_output, 0.1, "Test",
                value=validator(self.agent_output)
            )
        ):
            result = context_creation.context_develop(
                argparse.Namespace(name="example", agent=None)
            )

        self.assertEqual(result, 0)
        loaded = contexts.load_context("example")
        self.assertIn("Example is a small test project", loaded.identity)
        self.assertNotIn(context_creation.PLACEHOLDER_POINT, loaded.identity)

        identity = self.project_context_root / "example" / "local" / "identity.md"
        identity.write_text("# Manual\n\n- Keep this.\n", encoding="utf-8")
        with patch.object(
            context_creation,
            "invoke",
            side_effect=lambda invocation, validator, **_kwargs: AIInvocationResult(
                invocation, 0, self.agent_output, 0.1, "Test",
                value=validator(self.agent_output)
            )
        ):
            result = context_creation.context_develop(
                argparse.Namespace(name="example", agent=None)
            )
        self.assertEqual(result, 1)
        self.assertEqual(identity.read_text(encoding="utf-8"), "# Manual\n\n- Keep this.\n")

    def test_generated_identity_and_state_use_notes_and_bullet_points(self):
        output = json.dumps({
            "identity": "# Identity\n\nStable purpose.\n\n- Intended audience.",
            "state": "# State\n\nCurrent capability.\nContinued detail."
        })

        documents = context_creation._parse_agent_draft(output, self.project)

        self.assertEqual(
            documents["identity"],
            "# Identity\n\n"
            f"<!-- {context_creation.DOCUMENT_NOTES['identity']} -->\n\n"
            "- Stable purpose.\n- Intended audience.\n"
        )
        self.assertEqual(
            documents["state"],
            "# State\n\n"
            f"<!-- {context_creation.DOCUMENT_NOTES['state']} -->\n\n"
            "- Current capability. Continued detail.\n"
        )

    def test_enrichment_flattens_sections_and_discards_model_comments(self):
        output = json.dumps({
            "identity": (
                "# Dotfiles\n\n<!-- model note -->\n\nPurpose:\n"
                "- Portable configuration.\n"
            ),
            "state": (
                "# Dotfiles State\n\nCurrent Capabilities:\n"
                "- Provides terminal configuration\n"
                "- Defines window manager layout\n\n"
                "## Limitations\n\n- No installer\n"
            )
        })

        documents = context_creation._parse_agent_draft(output, self.project)

        self.assertEqual(
            documents["identity"],
            "# Dotfiles\n\n"
            f"<!-- {context_creation.DOCUMENT_NOTES['identity']} -->\n\n"
            "- Purpose: Portable configuration.\n"
        )
        self.assertEqual(
            documents["state"],
            "# Dotfiles State\n\n"
            f"<!-- {context_creation.DOCUMENT_NOTES['state']} -->\n\n"
            "- Current Capabilities: Provides terminal configuration\n"
            "- Current Capabilities: Defines window manager layout\n"
            "- Limitations: No installer\n"
        )
        for document in documents.values():
            body = document.split("-->\n\n", 1)[1]
            self.assertTrue(all(line.startswith("- ") for line in body.splitlines()))

    def test_decline_happens_after_full_preview_and_writes_nothing(self):
        preview_seen = []

        def decline(_prompt):
            preview_seen.extend(
                call.args[0]
                for call in rot_say.call_args_list
            )
            return "n"

        with patch.object(context_creation, "rot_say") as rot_say, patch.object(
            context_creation,
            "rot_continue"
        ), patch("builtins.input", side_effect=decline):
            result = context_creation._add_project_context(self.args())

        self.assertEqual(result, 0)
        self.assertTrue(any("PROPOSED identity.md" in item for item in preview_seen))
        self.assertTrue(any("PROPOSED state.md" in item for item in preview_seen))
        self.assertTrue(any("PROPOSED vision.md" in item for item in preview_seen))
        self.assertTrue(any("PROPOSED match.toml" in item for item in preview_seen))
        self.assertFalse((self.project_context_root / "example").exists())
        self.assertFalse(config_path().exists())

    def test_approved_creation_writes_project_files_and_registers_source(self):
        existing_config = config_path()
        existing_config.parent.mkdir(parents=True)
        existing_config.write_text('theme = "keep"\n', encoding="utf-8")

        result, _agent, _rot_say, rot_continue = self.run_add(answer="yes")

        self.assertEqual(result, 0)
        destination = self.project_context_root / "example"
        self.assertEqual(
            {path.name for path in destination.iterdir()},
            {"metadata.toml", "local", "shareable"}
        )
        vision = destination / "local" / "vision.md"
        self.assertEqual(
            vision.read_text(encoding="utf-8"),
            context_creation.INITIAL_VISION
        )
        self.assertIn("<!--", context_creation.INITIAL_VISION)
        self.assertIn("future ideas and vision", context_creation.INITIAL_VISION.lower())
        self.assertTrue(any(
            "projects/example/local/identity.md" in item.args[0]
            for item in rot_continue.call_args_list
        ))
        loaded = contexts.load_context("example")
        self.assertIn("Example is a small test project", loaded.identity)
        self.assertIn("Python entry point", loaded.state)
        match_text = (destination / "local" / "match.toml").read_text(encoding="utf-8")
        definition = context_matching.parse_match_toml(match_text)
        self.assertTrue(definition.source.is_git_repo)
        self.assertIn("github.com/example/project", definition.source.git_remotes)
        self.assertIn("main.py", definition.source.required_paths)
        self.assertIn("src/", definition.source.required_paths)
        self.assertIsNone(definition.production)
        self.assertNotIn(str(self.project), match_text)
        prompt = contexts.build_context_prompt("example")
        self.assertNotIn("github.com/example/project", prompt)
        self.assertNotIn(str(self.project), prompt)
        self.assertIn('theme = "keep"', existing_config.read_text(encoding="utf-8"))
        self.assertEqual(
            get_context_binding("example")["source_path"],
            str(self.project.resolve())
        )

        match = context_matching.match_contexts(
            self.project,
            name="example",
            binding_type="source",
            caddy_paths=()
        )[0]
        self.assertTrue(match.strong)

    def test_binding_failure_rolls_back_only_new_context(self):
        with patch.object(
            context_creation,
            "set_context_binding",
            side_effect=ConfigError("binding failed")
        ):
            result, _agent, _rot_say, _rot_continue = self.run_add(answer="yes")

        self.assertEqual(result, 1)
        self.assertFalse((self.project_context_root / "example").exists())
        self.assertFalse(config_path().exists())

    def test_file_failure_leaves_configuration_unchanged(self):
        config = config_path()
        config.parent.mkdir(parents=True)
        original = 'theme = "unchanged"\n'
        config.write_text(original, encoding="utf-8")

        with patch.object(
            context_creation,
            "_write_document",
            side_effect=OSError("write failed")
        ):
            result, _agent, _rot_say, _rot_continue = self.run_add(answer="yes")

        self.assertEqual(result, 1)
        self.assertFalse((self.project_context_root / "example").exists())
        self.assertEqual(config.read_text(encoding="utf-8"), original)

    def test_context_appearance_race_prevents_files_and_binding(self):
        destination = self.project_context_root / "example"

        def create_racing_context(_prompt):
            destination.mkdir()
            (destination / "marker").write_text("other process", encoding="utf-8")
            return "yes"

        with patch.object(context_creation, "rot_say"), patch.object(
            context_creation,
            "rot_continue"
        ), patch("builtins.input", side_effect=create_racing_context):
            result = context_creation._add_project_context(self.args())

        self.assertEqual(result, 1)
        self.assertEqual(
            (destination / "marker").read_text(encoding="utf-8"),
            "other process"
        )
        self.assertFalse(config_path().exists())

    def test_project_match_is_revalidated_after_confirmation(self):
        def change_remote(_prompt):
            subprocess.run(
                [
                    "git", "remote", "set-url", "upstream",
                    "git@github.com:example/different.git"
                ],
                cwd=self.project,
                check=True
            )
            return "yes"

        with patch.object(context_creation, "rot_say"), patch.object(
            context_creation,
            "rot_continue"
        ), patch("builtins.input", side_effect=change_remote):
            result = context_creation._add_project_context(self.args())

        self.assertEqual(result, 1)
        self.assertFalse((self.project_context_root / "example").exists())
        self.assertFalse(config_path().exists())

    def test_malformed_config_and_conflicting_binding_block_agent(self):
        config = config_path()
        config.parent.mkdir(parents=True)
        config.write_text("[broken", encoding="utf-8")
        result, stream_agent, _rot_say, _rot_continue = self.run_add(answer="yes")
        self.assertEqual(result, 1)
        stream_agent.assert_not_called()

        config.write_text(
            "[contexts.example]\n"
            f'source_path = "{self.root / "other"}"\n',
            encoding="utf-8"
        )
        result, stream_agent, _rot_say, _rot_continue = self.run_add(answer="yes")
        self.assertEqual(result, 1)
        stream_agent.assert_not_called()

    def test_git_project_without_remote_can_generate_path_match(self):
        subprocess.run(
            ["git", "remote", "remove", "upstream"],
            cwd=self.project,
            check=True
        )

        result, stream_agent, _rot_say, _rot_continue = self.run_add(answer="yes")

        self.assertEqual(result, 0)
        stream_agent.assert_called_once()

    def test_non_git_project_can_generate_portable_match_and_binding(self):
        non_git = self.root / "plain-project"
        non_git.mkdir()
        (non_git / "project.toml").write_text("name = 'plain'\n", encoding="utf-8")
        (non_git / "src").mkdir()

        result, _agent, _rot_say, _rot_continue = self.run_add(
            answer="yes", args=self.args(name="plain", path=non_git)
        )

        self.assertEqual(result, 0)
        match_path = self.project_context_root / "plain" / "local" / "match.toml"
        content = match_path.read_text(encoding="utf-8")
        definition = context_matching.parse_match_toml(content)
        self.assertFalse(definition.source.is_git_repo)
        self.assertIn("project.toml", definition.source.required_paths)
        self.assertNotIn(str(non_git), content)
        self.assertEqual(get_context_binding("plain")["source_path"], str(non_git))

    def test_invalid_xdg_config_home_is_reported_before_agent(self):
        with patch.dict(os.environ, {"XDG_CONFIG_HOME": "relative"}, clear=True):
            result, stream_agent, _rot_say, _rot_continue = self.run_add(answer="yes")

        self.assertEqual(result, 1)
        stream_agent.assert_not_called()


class ContextQuestionnaireTests(unittest.TestCase):
    def test_context_type_and_person_role_menus_can_exit(self):
        for answers in (
            ("exit",),
            ("4",),
            ("person", "alex", "q"),
            ("person", "alex", "4")
        ):
            with self.subTest(answers=answers), patch(
                "builtins.input",
                side_effect=answers
            ), patch.object(
                context_creation,
                "_add_project_context"
            ) as add_project, patch.object(
                context_creation,
                "_add_person_context"
            ) as add_person, patch.object(
                context_creation,
                "_add_machine_context"
            ) as add_machine, patch.object(context_creation, "rot_say"):
                result = context_creation.context_add(argparse.Namespace(agent=None))

            self.assertEqual(result, 0)
            add_project.assert_not_called()
            add_person.assert_not_called()
            add_machine.assert_not_called()

    def test_project_questions_route_name_path_and_agent(self):
        answers = ("project", "/srv/example", "example")

        with patch("builtins.input", side_effect=answers), patch.object(
            context_creation,
            "_add_project_context",
            return_value=7
        ) as add_project, patch.object(context_creation, "rot_say"):
            result = context_creation.context_add(argparse.Namespace(agent="codex"))

        self.assertEqual(result, 7)
        project_args = add_project.call_args.args[0]
        self.assertEqual(project_args.name, "example")
        self.assertEqual(project_args.path, "/srv/example")
        self.assertEqual(project_args.agent, "codex")

    def test_person_questions_route_role_and_display_name_without_agent(self):
        answers = (
            "person", "alex", "user", "Alex Example", "1,2", "yes"
        )

        with patch("builtins.input", side_effect=answers), patch.object(
            context_creation.loader,
            "list_contexts",
            return_value=("rotbot", "signalrot")
        ), patch.object(
            context_creation.entities,
            "create_entity_context",
            return_value=Path("context/users/alex")
        ) as create_entity, patch.object(
            context_creation,
            "rot_say"
        ), patch.object(context_creation, "rot_continue"):
            result = context_creation.context_add(argparse.Namespace(agent="codex"))

        self.assertEqual(result, 0)
        entity = create_entity.call_args.args[0]
        self.assertIsInstance(entity, context_creation.entities.UserContext)
        self.assertEqual(entity.name, "alex")
        self.assertEqual(entity.display_name, "Alex Example")
        self.assertEqual(entity.related_projects, ("rotbot", "signalrot"))

    def test_person_questions_route_assistant_role(self):
        answers = ("person", "rot", "assistant", "Rot", "", "yes")

        with patch("builtins.input", side_effect=answers), patch.object(
            context_creation.loader,
            "list_contexts",
            return_value=("rotbot", "signalrot")
        ), patch.object(
            context_creation.entities,
            "create_entity_context",
            return_value=Path("context/assistants/rot")
        ) as create_entity, patch.object(
            context_creation,
            "rot_say"
        ), patch.object(context_creation, "rot_continue"):
            result = context_creation.context_add(argparse.Namespace(agent=None))

        self.assertEqual(result, 0)
        entity = create_entity.call_args.args[0]
        self.assertIsInstance(entity, context_creation.entities.AssistantContext)
        self.assertEqual((entity.name, entity.display_name), ("rot", "Rot"))

    def test_preselected_user_reuses_person_creation_workflow(self):
        answers = ("", "yes")
        args = argparse.Namespace(
            agent=None,
            context_type="user",
            name="kamaji"
        )

        with patch("builtins.input", side_effect=answers), patch.object(
            context_creation.loader,
            "list_contexts",
            return_value=()
        ), patch.object(
            context_creation.entities,
            "create_entity_context",
            return_value=Path("context/users/kamaji")
        ) as create_entity, patch.object(
            context_creation,
            "rot_say"
        ), patch.object(context_creation, "rot_continue"):
            result = context_creation.context_add(args)

        self.assertEqual(result, 0)
        entity = create_entity.call_args.args[0]
        self.assertIsInstance(entity, context_creation.entities.UserContext)
        self.assertEqual((entity.name, entity.display_name), ("kamaji", "kamaji"))

    def test_person_questions_apply_contact_and_display_defaults(self):
        answers = ("2", "sam", "", "", "", "yes")

        with patch("builtins.input", side_effect=answers), patch.object(
            context_creation.loader,
            "list_contexts",
            return_value=("rotbot",)
        ), patch.object(
            context_creation.people,
            "create_person_context",
            return_value=Path("context/people/contact/sam")
        ) as create_person, patch.object(
            context_creation,
            "rot_say"
        ) as rot_say, patch.object(context_creation, "rot_continue"):
            result = context_creation.context_add(argparse.Namespace(agent=None))

        self.assertEqual(result, 0)
        create_person.assert_called_once_with(
            "sam", "contact", "sam", related_projects=(), context_id=ANY
        )
        self.assertTrue(any(
            "Leave blank to use their context name: sam" in call.args[0]
            for call in rot_say.call_args_list
        ))

    def test_machine_leave_empty_skips_inspection_and_local_metadata(self):
        answers = ("machine", "desktop", "", "empty", "yes")

        with patch("builtins.input", side_effect=answers), patch.object(
            context_creation,
            "inspect_local_machine"
        ) as inspect, patch.object(
            context_creation.machines,
            "create_machine",
            return_value=Path("context/machines/desktop")
        ) as create_machine, patch.object(
            context_creation.machines,
            "create_local_machine_record"
        ) as create_local, patch.object(
            context_creation,
            "rot_say"
        ), patch.object(context_creation, "rot_continue"):
            result = context_creation.context_add(argparse.Namespace(agent=None))

        self.assertEqual(result, 0)
        inspect.assert_not_called()
        create_machine.assert_called_once_with("desktop", "Desktop", None, ANY)
        create_local.assert_not_called()

    def test_machine_inspection_uses_approved_portable_and_declines_local_by_default(self):
        facts = MachineInspection(
            {"operating_system": "CachyOS", "architecture": "x86_64"},
            {"connection": {"hostname": "desktop-host"}}
        )
        answers = ("machine", "desktop", "Main Desktop", "inspect", "", "", "yes")

        with patch("builtins.input", side_effect=answers), patch.object(
            context_creation,
            "inspect_local_machine",
            return_value=facts
        ) as inspect, patch.object(
            context_creation,
            "show_inspection"
        ) as show, patch.object(
            context_creation.machines,
            "create_machine",
            return_value=Path("context/machines/desktop")
        ) as create_machine, patch.object(
            context_creation.machines,
            "create_local_machine_record"
        ) as create_local, patch.object(
            context_creation,
            "rot_say"
        ), patch.object(context_creation, "rot_continue"):
            result = context_creation.context_add(argparse.Namespace(agent=None))

        self.assertEqual(result, 0)
        inspect.assert_called_once_with()
        show.assert_called_once_with(facts)
        create_machine.assert_called_once_with(
            "desktop", "Main Desktop", facts.portable, ANY
        )
        create_local.assert_not_called()

    def test_preselected_machine_name_can_approve_local_metadata(self):
        facts = MachineInspection(
            {"architecture": "x86_64"},
            {"connection": {"hostname": "desktop-host"}}
        )
        answers = ("", "inspect", "", "yes", "yes")
        args = argparse.Namespace(
            agent=None,
            context_type="machine",
            name="desktop"
        )

        with patch("builtins.input", side_effect=answers), patch.object(
            context_creation,
            "inspect_local_machine",
            return_value=facts
        ), patch.object(context_creation, "show_inspection"), patch.object(
            context_creation.machines,
            "create_machine",
            return_value=Path("context/machines/desktop")
        ) as create_machine, patch.object(
            context_creation.machines,
            "create_local_machine_record",
            return_value=Path("config/rot/machines/desktop.toml")
        ) as create_local, patch.object(
            context_creation,
            "rot_say"
        ), patch.object(context_creation, "rot_continue"):
            result = context_creation.context_add(args)

        self.assertEqual(result, 0)
        create_machine.assert_called_once_with("desktop", "Desktop", facts.portable, ANY)
        create_local.assert_called_once_with("desktop", facts.local, ANY)

    def test_declined_portable_facts_are_not_written(self):
        facts = MachineInspection(
            {"operating_system": "CachyOS"},
            {}
        )
        answers = ("machine", "desktop", "", "inspect", "no", "yes")

        with patch("builtins.input", side_effect=answers), patch.object(
            context_creation,
            "inspect_local_machine",
            return_value=facts
        ), patch.object(context_creation, "show_inspection"), patch.object(
            context_creation.machines,
            "create_machine",
            return_value=Path("context/machines/desktop")
        ) as create_machine, patch.object(
            context_creation.machines,
            "create_local_machine_record"
        ) as create_local, patch.object(
            context_creation,
            "rot_say"
        ), patch.object(context_creation, "rot_continue"):
            result = context_creation.context_add(argparse.Namespace(agent=None))

        self.assertEqual(result, 0)
        create_machine.assert_called_once_with("desktop", "Desktop", None, ANY)
        create_local.assert_not_called()

    def test_local_failure_preserves_created_portable_context(self):
        destination = Path("context/machines/desktop")
        facts = {"connection": {"hostname": "desktop-host"}}
        with patch("builtins.input", return_value="yes"), patch.object(
            context_creation.machines,
            "create_machine",
            return_value=destination
        ) as create_machine, patch.object(
            context_creation.machines,
            "create_local_machine_record",
            side_effect=context_creation.machines.MachineContextError("local failed")
        ), patch.object(context_creation, "rot_say") as rot_say, patch.object(
            context_creation,
            "rot_continue"
        ):
            result = context_creation._add_machine_context(
                "desktop", "Desktop", {}, facts, True
            )

        self.assertEqual(result, 1)
        create_machine.assert_called_once()
        self.assertIn("was created", rot_say.call_args.args[0])
        self.assertIn("local failed", rot_say.call_args.args[0])

    def test_declined_person_confirmation_creates_nothing(self):
        answers = ("person", "alex", "contact", "Alex", "", "no")

        with patch("builtins.input", side_effect=answers), patch.object(
            context_creation.loader,
            "list_contexts",
            return_value=("rotbot",)
        ), patch.object(
            context_creation.people,
            "create_person_context"
        ) as create_person, patch.object(
            context_creation,
            "rot_say"
        ), patch.object(context_creation, "rot_continue"):
            result = context_creation.context_add(argparse.Namespace(agent=None))

        self.assertEqual(result, 0)
        create_person.assert_not_called()

    def test_related_project_menu_can_exit_without_creation(self):
        answers = ("person", "alex", "contact", "Alex", "exit")

        with patch("builtins.input", side_effect=answers), patch.object(
            context_creation.loader,
            "list_contexts",
            return_value=("rotbot", "signalrot")
        ), patch.object(
            context_creation,
            "_add_person_context"
        ) as add_person, patch.object(context_creation, "rot_say"):
            result = context_creation.context_add(argparse.Namespace(agent=None))

        self.assertEqual(result, 0)
        add_person.assert_not_called()


if __name__ == "__main__":
    unittest.main()
