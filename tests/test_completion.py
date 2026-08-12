import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

from rotbot.cli import parser as command_parser
from rotbot.contexts import entities
from rotbot.session import completion, shell


class CompletionProviderTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.session = SimpleNamespace(cwd=self.root)
        shell.available_executables.cache_clear()

    def tearDown(self):
        shell.available_executables.cache_clear()
        self.temporary_directory.cleanup()

    def values(self, line, provider=None):
        provider = provider or completion.CompletionProvider(self.session)
        return [item.value for item in provider.complete(line)]

    def test_top_level_uses_parser_commands_and_session_builtins(self):
        values = self.values("con")

        self.assertEqual(values, ["context "])
        self.assertIn("help ", self.values("he"))

    def test_new_parser_command_is_discovered_without_completion_map(self):
        def parser_factory():
            parser = command_parser.RotArgumentParser()
            parser.add_subparsers().add_parser("new-command")
            return parser

        provider = completion.CompletionProvider(self.session, parser_factory)

        self.assertIn("new-command ", self.values("new", provider))

    def test_subcommands_and_options_are_derived_from_parser(self):
        self.assertEqual(
            self.values("context "),
            [value + " " for value in ("add", "bind", "delete", "inspect", "list", "mod", "show")]
        )
        self.assertIn("--agent ", self.values("ask --"))
        self.assertEqual(self.values("ask --agent c"), ["codex "])

    def test_executable_completion_filters_and_deduplicates(self):
        first = self.root / "bin-one"
        second = self.root / "bin-two"
        first.mkdir()
        second.mkdir()
        for directory in (first, second):
            executable = directory / "system-test"
            executable.write_text("#!/bin/sh\n", encoding="utf-8")
            executable.chmod(0o700)
        non_executable = first / "system-note"
        non_executable.write_text("no", encoding="utf-8")
        with patch.dict(os.environ, {"PATH": os.pathsep.join((str(first), str(second)))}):
            values = self.values("syst")

        self.assertEqual(values, ["system-test "])

    def test_path_change_uses_separate_executable_cache_key(self):
        first = self.root / "first"
        second = self.root / "second"
        first.mkdir()
        second.mkdir()
        for directory, name in ((first, "alpha-tool"), (second, "beta-tool")):
            path = directory / name
            path.write_text("#!/bin/sh\n", encoding="utf-8")
            path.chmod(0o700)
        with patch.dict(os.environ, {"PATH": str(first)}):
            self.assertIn("alpha-tool ", self.values("alpha"))
        with patch.dict(os.environ, {"PATH": str(second)}):
            self.assertIn("beta-tool ", self.values("beta"))

    def test_relative_file_directory_cd_home_parent_and_spaces(self):
        (self.root / "README.md").write_text("readme", encoding="utf-8")
        (self.root / "Documents").mkdir()
        (self.root / "space name.txt").write_text("space", encoding="utf-8")
        child = self.root / "child"
        child.mkdir()

        self.assertIn("README.md", self.values("cat READ"))
        self.assertIn("Documents/", self.values("cd Doc"))
        self.assertNotIn("README.md", self.values("cd READ"))
        self.assertIn("space\\ name.txt", self.values("cat spa"))
        self.assertIn('"space name.txt" ', self.values('cat "spa'))

        self.session.cwd = child
        self.assertIn("../README.md", self.values("cat ../READ"))
        with patch("rotbot.session.completion.os.path.expanduser", return_value=str(self.root / "Doc")):
            self.assertIn("~/Documents/", self.values("cd ~/Doc"))

    def test_context_names_come_from_first_class_registries(self):
        with patch.object(
            completion.loader, "list_contexts", return_value=("rotbot", "signalrot")
        ), patch.object(
            completion.entities, "list_user_contexts",
            return_value=(entities.UserContext("Kamaji", "Kamaji", (), "u"),)
        ), patch.object(
            completion.entities, "list_assistant_contexts",
            return_value=(entities.AssistantContext("rot", "rot", (), "a"),)
        ), patch.object(
            completion.machines, "list_machine_contexts", return_value=()
        ), patch.object(
            completion.people, "list_person_contexts", return_value=()
        ):
            values = self.values("context show r")

        self.assertEqual(values, ["rot ", "rotbot "])

    def test_completion_does_not_dispatch_execute_or_invoke_ai(self):
        with patch.object(command_parser, "parse_args") as parse, patch(
            "rotbot.session.shell.run_shell"
        ) as run_shell, patch(
            "rotbot.agents.runner.stream_agent"
        ) as stream_agent:
            before = Path.cwd()
            self.values("context ")
            self.values("cat READ")

        parse.assert_not_called()
        run_shell.assert_not_called()
        stream_agent.assert_not_called()
        self.assertEqual(Path.cwd(), before)

    def test_incomplete_input_and_provider_failures_are_silent(self):
        with patch.object(
            completion.entities, "list_user_contexts", side_effect=OSError("gone")
        ):
            self.assertEqual(self.values('context show "ro'), [])


if __name__ == "__main__":
    unittest.main()
