from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from rotbot.contexts import loader as contexts
from rotbot.contexts import machines
from rotbot.contexts import matching as context_matching


SOURCE_ONLY = """[source]
is_git_repo = true
git_remotes = ["github.com/example/project"]
required_paths = ["parser.py", "context/"]
optional_paths = ["README.md"]
"""

WITH_PRODUCTION = SOURCE_ONLY + """
[production]
domains = ["example.net"]
required_paths = ["index.html", "assets/"]
"""

NON_GIT = """[source]
is_git_repo = false
required_paths = ["project.toml", "src/"]
optional_paths = ["README.md"]
"""


class ContextMatchingTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.context_root = self.root / "contexts"
        self.context_root.mkdir()
        self.project_context_root = self.context_root / "projects"
        self.project_context_root.mkdir()
        self.machine_context_root = self.context_root / "machines"
        self.machine_context_root.mkdir()
        self.context_patch = patch.object(contexts, "CONTEXT_ROOT", self.context_root)
        self.context_patch.start()

    def tearDown(self):
        self.context_patch.stop()
        self.temporary_directory.cleanup()

    def create_context(self, name, match=None):
        directory = self.project_context_root / name
        directory.mkdir()
        (directory / "identity.md").write_text(f"{name} identity", encoding="utf-8")
        (directory / "state.md").write_text(f"{name} state", encoding="utf-8")
        if match is not None:
            (directory / "match.toml").write_text(match, encoding="utf-8")
        return directory

    def create_repository(self, remote, remote_name="upstream", directory_name="repository"):
        repository = self.root / directory_name
        repository.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=repository, check=True)
        subprocess.run(
            ["git", "remote", "add", remote_name, remote],
            cwd=repository,
            check=True
        )
        (repository / "parser.py").write_text("", encoding="utf-8")
        (repository / "context").mkdir()
        return repository

    def test_machine_contexts_are_not_project_match_candidates(self):
        destination = machines.create_machine(
            "machine-only", machines_root=self.machine_context_root
        )
        (destination / "match.toml").write_text(SOURCE_ONLY, encoding="utf-8")
        repository = self.create_repository("git@github.com:example/project.git")

        candidates = context_matching.match_contexts(repository, caddy_paths=())

        self.assertEqual(candidates, ())

    def test_parser_supports_git_non_git_and_production_definitions(self):
        source = context_matching.parse_match_toml(SOURCE_ONLY)
        non_git = context_matching.parse_match_toml(NON_GIT)
        combined = context_matching.parse_match_toml(WITH_PRODUCTION)

        self.assertTrue(source.source.is_git_repo)
        self.assertEqual(source.source.git_remotes, ("github.com/example/project",))
        self.assertFalse(non_git.source.is_git_repo)
        self.assertEqual(non_git.source.required_paths, ("project.toml", "src/"))
        self.assertIsNone(source.production)
        self.assertEqual(combined.production.domains, ("example.net",))
        self.assertEqual(combined.production.required_paths, ("index.html", "assets/"))

    def test_parser_rejects_malformed_or_unsupported_documents(self):
        documents = (
            "unknown = true\n",
            "[source]\nrequired_paths = [\"file\"]\n",
            "[source]\nis_git_repo = false\nrequired_paths = []\n",
            "[source]\nis_git_repo = false\nrequired_paths = [\"file\"]\n"
            "git_remotes = [\"github.com/a/b\"]\n",
            SOURCE_ONLY + "unsupported = true\n",
            SOURCE_ONLY.replace('"parser.py"', '"../parser.py"')
        )
        for document in documents:
            with self.subTest(document=document), self.assertRaises(context_matching.MatchError):
                context_matching.parse_match_toml(document)

    def test_legacy_markdown_match_remains_loadable(self):
        directory = self.create_context("legacy")
        (directory / "match.md").write_text(
            "# Match\n\n## Source\n\nGit remotes:\n"
            "- github.com/example/project\n\nRequired paths:\n- parser.py\n",
            encoding="utf-8"
        )

        definition = context_matching.load_match_definition("legacy")

        self.assertTrue(definition.source.is_git_repo)
        self.assertEqual(definition.source.required_paths, ("parser.py",))

    def test_context_without_match_remains_valid_and_prompts_exclude_match(self):
        self.create_context("plain")
        self.create_context("matched", SOURCE_ONLY)

        self.assertEqual(contexts.list_contexts(), ("matched", "plain"))
        self.assertIsNone(context_matching.load_match_definition("plain"))
        prompt = contexts.build_context_prompt("matched")
        self.assertNotIn("github.com/example/project", prompt)
        self.assertNotIn("Required paths", prompt)

    def test_git_remote_normalization_equates_common_formats(self):
        remotes = (
            "git@github.com:0xkamaji/rotbot.git",
            "https://github.com/0xkamaji/rotbot.git",
            "ssh://git@github.com/0xkamaji/rotbot.git",
            "github.com/0xkamaji/rotbot"
        )
        self.assertEqual(
            {context_matching.normalize_git_remote(remote) for remote in remotes},
            {"github.com/0xkamaji/rotbot"}
        )
        self.assertIsNone(context_matching.normalize_git_remote("http://["))

    def test_source_matching_uses_non_origin_remote_and_required_paths(self):
        self.create_context("project", SOURCE_ONLY)
        repository = self.create_repository("git@github.com:example/project.git")

        candidates = context_matching.match_contexts(
            repository,
            name="project",
            binding_type="source",
            caddy_paths=()
        )

        self.assertEqual(len(candidates), 1)
        self.assertTrue(candidates[0].strong)
        self.assertTrue(any("upstream" in item.message for item in candidates[0].evidence))

    def test_source_matching_inspects_push_urls(self):
        self.create_context("project", SOURCE_ONLY)
        repository = self.create_repository("github.com/example/wrong", "origin")
        subprocess.run(
            [
                "git", "remote", "set-url", "--add", "--push", "origin",
                "git@github.com:example/project.git"
            ],
            cwd=repository,
            check=True
        )

        candidate = context_matching.match_contexts(
            repository,
            name="project",
            binding_type="source",
            caddy_paths=()
        )[0]

        self.assertTrue(candidate.strong)

    def test_explicit_source_matching_does_not_inspect_caddy(self):
        self.create_context("project", WITH_PRODUCTION)
        repository = self.create_repository("github.com/example/project")

        with patch.object(context_matching, "_caddy_sites") as caddy_sites:
            candidate = context_matching.match_contexts(
                repository,
                name="project",
                binding_type="source"
            )[0]

        self.assertTrue(candidate.strong)
        caddy_sites.assert_not_called()

    def test_remote_match_fails_when_required_path_is_missing(self):
        self.create_context("project", SOURCE_ONLY)
        repository = self.create_repository("github.com/example/project")
        (repository / "parser.py").unlink()

        candidate = context_matching.match_contexts(
            repository,
            name="project",
            binding_type="source",
            caddy_paths=()
        )[0]

        self.assertFalse(candidate.strong)
        self.assertTrue(any(item.message == "Missing parser.py" for item in candidate.evidence))

    def test_non_git_project_matches_from_portable_paths_at_a_different_location(self):
        self.create_context("portable", NON_GIT)
        original = self.root / "original"
        relocated = self.root / "relocated"
        for directory in (original, relocated):
            directory.mkdir()
            (directory / "project.toml").write_text("name = 'portable'\n", encoding="utf-8")
            (directory / "src").mkdir()

        original_match = context_matching.match_contexts(
            original, name="portable", binding_type="source", caddy_paths=()
        )[0]
        relocated_match = context_matching.match_contexts(
            relocated, name="portable", binding_type="source", caddy_paths=()
        )[0]

        self.assertTrue(original_match.strong)
        self.assertTrue(relocated_match.strong)
        self.assertNotIn(str(original), NON_GIT)

    def test_git_state_is_a_required_signal_for_source_matching(self):
        self.create_context("non-git", NON_GIT)
        repository = self.root / "git-project"
        repository.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=repository, check=True)
        (repository / "project.toml").write_text("", encoding="utf-8")
        (repository / "src").mkdir()

        candidate = context_matching.match_contexts(
            repository, name="non-git", binding_type="source", caddy_paths=()
        )[0]

        self.assertFalse(candidate.strong)

    def test_contexts_match_only_their_declared_remotes(self):
        self.create_context("rotbot", SOURCE_ONLY.replace("example/project", "0xkamaji/rotbot"))
        self.create_context(
            "signalrot",
            SOURCE_ONLY.replace("example/project", "0xkamaji/signalrot")
        )
        repository = self.create_repository("git@github.com:0xkamaji/rotbot.git")

        candidates = context_matching.match_contexts(repository, caddy_paths=())
        strong = [(item.name, item.binding_type) for item in candidates if item.strong]

        self.assertEqual(strong, [("rotbot", "source")])

        signalrot_repository = self.create_repository(
            "git@github.com:0xkamaji/signalrot.git",
            directory_name="signalrot-repository"
        )
        signalrot_candidates = context_matching.match_contexts(
            signalrot_repository,
            caddy_paths=()
        )
        signalrot_strong = [
            (item.name, item.binding_type)
            for item in signalrot_candidates
            if item.strong
        ]
        self.assertEqual(signalrot_strong, [("signalrot", "source")])

    def test_production_matching_uses_caddy_domain_root_and_required_paths(self):
        self.create_context("project", WITH_PRODUCTION)
        production = self.root / "production"
        production.mkdir()
        (production / "index.html").write_text("", encoding="utf-8")
        (production / "assets").mkdir()
        caddyfile = self.root / "Caddyfile"
        caddyfile.write_text(
            f"example.net {{\n    root * {production}\n    file_server\n}}\n",
            encoding="utf-8"
        )

        candidate = context_matching.match_contexts(
            production,
            name="project",
            binding_type="production",
            caddy_paths=(caddyfile,)
        )[0]

        self.assertTrue(candidate.strong)
        self.assertTrue(any("example.net" in item.message for item in candidate.evidence))

    def test_production_is_not_strong_when_caddy_is_unavailable(self):
        self.create_context("project", WITH_PRODUCTION)
        production = self.root / "production"
        production.mkdir()
        (production / "index.html").write_text("", encoding="utf-8")
        (production / "assets").mkdir()

        candidate = context_matching.match_contexts(
            production,
            name="project",
            binding_type="production",
            caddy_paths=(self.root / "missing-Caddyfile",)
        )[0]

        self.assertFalse(candidate.strong)
        self.assertTrue(any("unavailable" in item.message for item in candidate.evidence))

    def test_explicit_filters_and_ambiguous_matches(self):
        self.create_context("first", SOURCE_ONLY)
        self.create_context("second", SOURCE_ONLY)
        repository = self.create_repository("github.com/example/project")

        all_candidates = context_matching.match_contexts(repository, caddy_paths=())
        explicit = context_matching.match_contexts(
            repository,
            name="first",
            binding_type="source",
            caddy_paths=()
        )

        self.assertEqual(sum(candidate.strong for candidate in all_candidates), 2)
        self.assertEqual([(item.name, item.binding_type) for item in explicit], [("first", "source")])

    def test_match_document_and_required_path_symlinks_are_rejected(self):
        directory = self.create_context("project")
        outside_match = self.root / "match.toml"
        outside_match.write_text(SOURCE_ONLY, encoding="utf-8")
        (directory / "match.toml").symlink_to(outside_match)
        with self.assertRaises(context_matching.MatchError):
            context_matching.load_match_definition("project")

        (directory / "match.toml").unlink()
        (directory / "match.toml").write_text(SOURCE_ONLY, encoding="utf-8")
        repository = self.create_repository("github.com/example/project")
        (repository / "context").rmdir()
        outside = self.root / "outside"
        outside.mkdir()
        (repository / "context").symlink_to(outside, target_is_directory=True)
        candidate = context_matching.match_contexts(
            repository,
            name="project",
            binding_type="source",
            caddy_paths=()
        )[0]
        self.assertFalse(candidate.strong)

    def test_matching_never_invokes_an_agent(self):
        self.create_context("project", SOURCE_ONLY)
        repository = self.create_repository("github.com/example/project")
        with patch("rotbot.agents.invocation.invoke") as stream_agent:
            context_matching.match_contexts(repository, caddy_paths=())
        stream_agent.assert_not_called()


if __name__ == "__main__":
    unittest.main()
