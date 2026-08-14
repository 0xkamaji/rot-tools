import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from rotbot.contexts.inspection import IdentificationSources, InspectedContext
from rotbot.contexts.paths import state_root
from rotbot.session.state import (
    SessionState,
    SessionStateError,
    SessionStateStore,
    session_state_path
)


class SessionStateStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.state_home = self.root / "state"
        self.environment = {"XDG_STATE_HOME": str(self.state_home)}
        self.inspected = InspectedContext(
            "Rot", "assistant-id",
            "Kamaji", "user-id",
            "laptop", "machine-id",
            "rotbot", "project-id",
            Path("/work/rotbot"),
            IdentificationSources(
                "local config", "local config", "local config", "source binding"
            ),
            ("not persisted",)
        )

    def tearDown(self):
        self.temporary.cleanup()

    def test_default_path_uses_xdg_state_home(self):
        self.assertEqual(state_root(self.environment), self.state_home / "rotbot")
        self.assertEqual(
            session_state_path(self.environment),
            self.state_home / "rotbot" / "session.toml"
        )

    def test_round_trip_preserves_active_context_references(self):
        path = session_state_path(self.environment)
        store = SessionStateStore(path)
        state = SessionState.from_inspected(self.inspected)

        store.save(state)
        loaded = store.load()

        self.assertEqual(loaded, state)
        self.assertEqual(loaded.to_inspected().cwd, Path("/work/rotbot"))
        self.assertEqual(loaded.user, "Kamaji")
        self.assertEqual(loaded.user_id, "user-id")
        self.assertEqual(loaded.user_source, "local config")
        self.assertNotIn("not persisted", path.read_text(encoding="utf-8"))
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)

    def test_missing_optional_contexts_round_trip(self):
        inspected = InspectedContext(
            None, None, None, None, None, None, None, None,
            Path("/work"),
            IdentificationSources(
                "not configured", "not configured", "not configured", "none"
            ),
            ()
        )
        store = SessionStateStore(session_state_path(self.environment))

        store.save(SessionState.from_inspected(inspected))

        self.assertEqual(store.load().to_inspected(), inspected)

    def test_rejects_malformed_or_unsafe_state_files(self):
        path = session_state_path(self.environment)
        path.parent.mkdir(parents=True)
        for content in ("[broken", "schema_version = 2\ncwd = \"/work\"\n"):
            with self.subTest(content=content):
                path.unlink(missing_ok=True)
                path.write_text(content, encoding="utf-8")
                if os.name != "nt":
                    os.chmod(path, 0o600)
                with self.assertRaises(SessionStateError):
                    SessionStateStore(path).load()

        path.unlink()
        target = self.root / "outside"
        target.write_text("outside", encoding="utf-8")
        path.symlink_to(target)
        with self.assertRaises(SessionStateError):
            SessionStateStore(path).load()

        path.unlink()
        unsafe = SessionState.from_inspected(
            self.inspected._replace(cwd=Path("/work/bad\x7fpath"))
        )
        with self.assertRaises(SessionStateError):
            SessionStateStore(path).save(unsafe)

    def test_failed_replace_preserves_original_and_cleans_temporary(self):
        path = session_state_path(self.environment)
        store = SessionStateStore(path)
        original = SessionState.from_inspected(self.inspected)
        store.save(original)
        before = path.read_bytes()
        updated = SessionState.from_inspected(
            self.inspected._replace(user="Alex", user_id="alex-id")
        )

        with patch("rotbot.session.state.os.replace", side_effect=OSError("failed")):
            with self.assertRaises(SessionStateError):
                store.save(updated)

        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(tuple(path.parent.glob("session.*.tmp")), ())


if __name__ == "__main__":
    unittest.main()
