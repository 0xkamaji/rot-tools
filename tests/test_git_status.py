import argparse
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from rotbot.commands import git as git_commands


class GitStatusTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.repository = self.root / "repository"
        self.repository.mkdir()
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "RotBot Test")
        self.git("config", "user.email", "rotbot@example.invalid")

    def tearDown(self):
        self.temporary_directory.cleanup()

    def git(self, *args, cwd=None, check=True):
        return subprocess.run(
            ["git", *args],
            cwd=cwd or self.repository,
            capture_output=True,
            text=True,
            check=check
        )

    def commit(self, subject="Initial commit"):
        tracked = self.repository / "tracked.txt"
        if not tracked.exists():
            tracked.write_text("initial\n", encoding="utf-8")
        self.git("add", ".")
        self.git("commit", "-q", "-m", subject)

    def run_status(self, fetch=False, repository=None):
        with patch.object(
            git_commands.os,
            "getcwd",
            return_value=str(repository or self.repository)
        ), patch.object(git_commands, "rot_say") as rot_say:
            result = git_commands.git_status(argparse.Namespace(fetch=fetch))
        message = rot_say.call_args.args[0]
        return result, message

    def add_remote(self):
        remote = self.root / "remote.git"
        self.git("init", "--bare", "-q", str(remote))
        self.git("remote", "add", "shared", str(remote))
        self.git("push", "-q", "-u", "shared", "main")
        self.git("symbolic-ref", "HEAD", "refs/heads/main", cwd=remote)
        return remote

    def clone_peer(self, remote):
        peer = self.root / "peer"
        self.git("clone", "-q", str(remote), str(peer), cwd=self.root)
        self.git("config", "user.name", "RotBot Peer", cwd=peer)
        self.git("config", "user.email", "peer@example.invalid", cwd=peer)
        return peer

    def test_clean_repository_without_upstream_and_last_commit(self):
        self.commit("A complete subject line")

        result, message = self.run_status()

        short_hash = self.git("rev-parse", "--short", "HEAD").stdout.strip()
        self.assertEqual(result, 0)
        self.assertIn("Repository: repository", message)
        self.assertIn("Branch:     main", message)
        self.assertIn("Upstream:   Not configured", message)
        self.assertIn("Working:    Clean", message)
        self.assertIn("Remote:     No upstream configured", message)
        self.assertIn(short_hash, message)
        self.assertRegex(message, rf"{short_hash} · .+")
        self.assertIn("A complete subject line", message)
        self.assertIn("Next: Configure an upstream branch", message)

    def test_worktree_counts_unique_files_and_overlapping_categories(self):
        self.commit()
        tracked = self.repository / "tracked.txt"
        tracked.write_text("staged\n", encoding="utf-8")
        self.git("add", "tracked.txt")
        tracked.write_text("staged and modified\n", encoding="utf-8")
        (self.repository / "file with spaces.txt").write_text("new\n", encoding="utf-8")

        result, message = self.run_status()

        self.assertEqual(result, 0)
        self.assertIn("Working:    2 changed", message)
        self.assertIn("1 staged · 1 modified · 1 untracked", message)
        self.assertIn("Next: Review changes with git diff", message)

    def test_staged_deleted_and_renamed_counts(self):
        self.commit()
        (self.repository / "delete.txt").write_text("delete\n", encoding="utf-8")
        self.git("add", "delete.txt")
        self.git("commit", "-q", "-m", "Add delete target")
        self.git("mv", "tracked.txt", "renamed.txt")
        (self.repository / "delete.txt").unlink()

        result, message = self.run_status()

        self.assertEqual(result, 0)
        self.assertIn("2 changed", message)
        self.assertIn("1 staged", message)
        self.assertIn("1 deleted", message)
        self.assertIn("1 renamed", message)

    def test_no_commits_and_detached_head(self):
        result, message = self.run_status()
        self.assertEqual(result, 0)
        self.assertIn("Last commit: None", message)
        self.assertIn("Next: Create the initial commit", message)

        self.commit()
        self.git("checkout", "-q", "--detach", "HEAD")
        result, message = self.run_status()
        short_hash = self.git("rev-parse", "--short", "HEAD").stdout.strip()
        self.assertEqual(result, 0)
        self.assertIn(f"Branch:     Detached at {short_hash}", message)
        self.assertIn("Upstream:   Not configured", message)
        self.assertIn("Next: Check out a branch", message)

    def test_default_status_does_not_refresh_the_index(self):
        self.commit()
        (self.repository / "tracked.txt").write_text("modified\n", encoding="utf-8")
        index = self.repository / ".git" / "index"
        before_content = index.read_bytes()
        before_time = index.stat().st_mtime_ns

        result, _message = self.run_status()

        self.assertEqual(result, 0)
        self.assertEqual(index.read_bytes(), before_content)
        self.assertEqual(index.stat().st_mtime_ns, before_time)

    def test_cached_up_to_date_and_ahead_states_never_fetch(self):
        self.commit()
        self.add_remote()
        real_run = subprocess.run
        calls = []

        def recording_run(command, **kwargs):
            calls.append(command)
            return real_run(command, **kwargs)

        with patch.object(git_commands.subprocess, "run", side_effect=recording_run):
            result, message = self.run_status()
        self.assertEqual(result, 0)
        self.assertIn("Up to date with cached shared/main", message)
        self.assertIn("Verify: rot git status --fetch", message)
        self.assertFalse(any(command[:2] == ["git", "fetch"] for command in calls))

        (self.repository / "ahead.txt").write_text("ahead\n", encoding="utf-8")
        self.git("add", ".")
        self.git("commit", "-q", "-m", "Ahead commit")
        result, message = self.run_status()
        self.assertEqual(result, 0)
        self.assertIn("Ahead of cached shared/main by 1 commit", message)
        self.assertIn("Next: rot push", message)

    def test_cached_behind_and_diverged_states(self):
        self.commit()
        remote = self.add_remote()
        peer = self.clone_peer(remote)
        (peer / "remote.txt").write_text("remote\n", encoding="utf-8")
        self.git("add", ".", cwd=peer)
        self.git("commit", "-q", "-m", "Remote commit", cwd=peer)
        self.git("push", "-q", cwd=peer)
        self.git("fetch", "-q", "shared")

        result, message = self.run_status()
        self.assertEqual(result, 0)
        self.assertIn("Behind cached shared/main by 1 commit", message)
        self.assertIn("Next: rot pull", message)

        (self.repository / "local.txt").write_text("local\n", encoding="utf-8")
        self.git("add", ".")
        self.git("commit", "-q", "-m", "Local commit")
        result, message = self.run_status()
        self.assertEqual(result, 0)
        self.assertIn("Diverged from cached shared/main: 1 ahead, 1 behind", message)
        self.assertIn("Review the divergent history", message)

    def test_fetch_refreshes_before_sync_calculation(self):
        self.commit()
        remote = self.add_remote()
        peer = self.clone_peer(remote)
        (peer / "remote.txt").write_text("remote\n", encoding="utf-8")
        self.git("add", ".", cwd=peer)
        self.git("commit", "-q", "-m", "Remote commit", cwd=peer)
        self.git("push", "-q", cwd=peer)

        real_run = subprocess.run
        calls = []

        def recording_run(command, **kwargs):
            calls.append(command)
            return real_run(command, **kwargs)

        with patch.object(git_commands.subprocess, "run", side_effect=recording_run):
            result, message = self.run_status(fetch=True)

        fetch_index = next(index for index, command in enumerate(calls) if command[:2] == ["git", "fetch"])
        compare_index = next(index for index, command in enumerate(calls) if command[:2] == ["git", "rev-list"])
        self.assertLess(fetch_index, compare_index)
        self.assertEqual(result, 0)
        self.assertIn("Behind by 1 commit", message)
        self.assertIn("Fetched:    Remote comparison refreshed", message)
        self.assertNotIn("cached", message.lower())

    def test_fetch_failure_does_not_claim_fresh_comparison(self):
        self.commit()
        self.add_remote()
        self.git("remote", "set-url", "shared", str(self.root / "missing.git"))

        result, message = self.run_status(fetch=True)

        self.assertEqual(result, 1)
        self.assertIn("Could not fetch upstream remote 'shared'", message)
        self.assertNotIn("refreshed", message.lower())

    def test_successful_fetch_with_missing_upstream_ref_is_not_verified(self):
        self.commit()
        self.add_remote()
        self.git("config", "branch.main.merge", "refs/heads/missing")

        result, message = self.run_status(fetch=True)

        self.assertEqual(result, 1)
        self.assertIn("upstream comparison is unavailable", message)
        self.assertNotIn("refreshed", message.lower())

    def test_merge_conflict_is_prioritized(self):
        self.commit()
        self.git("checkout", "-q", "-b", "other")
        (self.repository / "tracked.txt").write_text("other\n", encoding="utf-8")
        self.git("commit", "-qam", "Other change")
        self.git("checkout", "-q", "main")
        (self.repository / "tracked.txt").write_text("main\n", encoding="utf-8")
        self.git("commit", "-qam", "Main change")
        merge = self.git("merge", "other", check=False)
        self.assertNotEqual(merge.returncode, 0)

        result, message = self.run_status()

        self.assertEqual(result, 0)
        self.assertIn("1 conflicted", message)
        self.assertIn("Next: Resolve merge conflicts before continuing", message)

    def test_non_repository_and_git_unavailable(self):
        outside = self.root / "outside"
        outside.mkdir()
        result, message = self.run_status(repository=outside)
        self.assertEqual(result, 1)
        self.assertIn("not inside a Git repository", message)

        with patch.object(git_commands, "_capture_git", side_effect=FileNotFoundError), patch.object(
            git_commands,
            "rot_say"
        ) as rot_say:
            result = git_commands.git_status(argparse.Namespace(fetch=False))
        self.assertEqual(result, 127)
        self.assertIn("not installed", rot_say.call_args.args[0])

    def test_malformed_porcelain_is_rejected(self):
        malformed = (
            b"1 broken\0",
            b"2 R. N... 100644 100644 100644 a b R100 path\0",
            b"? \0",
            b"1 \xff. N... 100644 100644 100644 a b path\0"
        )
        for output in malformed:
            with self.subTest(output=output), self.assertRaises(git_commands.GitStatusError):
                git_commands._parse_porcelain_v2(output)


if __name__ == "__main__":
    unittest.main()
