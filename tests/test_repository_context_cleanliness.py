import subprocess
import unittest

from rotbot.contexts.paths import builtin_assistants_root, contexts_root, repository_root


class RepositoryContextCleanlinessTests(unittest.TestCase):
    def test_runtime_context_default_is_outside_repository(self):
        self.assertNotEqual(contexts_root(), repository_root() / "context")
        self.assertNotIn(repository_root(), contexts_root().parents)

    def test_repository_tracks_only_builtin_context_definition(self):
        tracked = subprocess.run(
            ["git", "ls-files", "context", "builtin"],
            cwd=repository_root(), capture_output=True, text=True, check=True
        ).stdout.splitlines()
        present = [path for path in tracked if repository_root().joinpath(path).exists()]
        self.assertFalse(any(path == "context" or path.startswith("context/") for path in present))
        self.assertTrue(all(path.startswith("builtin/assistants/rot/") for path in present))
        self.assertTrue(builtin_assistants_root().joinpath("rot/metadata.toml").is_file())


if __name__ == "__main__":
    unittest.main()
