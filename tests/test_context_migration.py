from pathlib import Path
import tempfile
import unittest

from rotbot.contexts.migration import migrate_contexts


class ContextMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        self.source = root / "context"
        self.destination = root / "runtime"
        self.source.mkdir()

    def tearDown(self):
        self.temporary_directory.cleanup()

    def make_entity(self, relative, name, files=None):
        entity = self.source / relative / name
        entity.mkdir(parents=True)
        metadata = (
            f'id = "00000000-0000-4000-8000-{len(name):012d}"\n'
            f'name = "{name}"\n'
            f'display_name = "{name.title()}"\n'
        ).encode()
        (entity / "metadata.toml").write_bytes(metadata)
        for filename, content in (files or {}).items():
            path = entity / filename
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        return entity, metadata

    def test_root_legacy_documents_default_to_private_and_structure_is_canonical(self):
        records = (
            ("users", "alex", "identity.md"),
            ("machines", "server", "notes.md"),
            ("projects", "rotbot", "vision.md"),
        )
        metadata = {}
        for category, name, filename in records:
            _, metadata[name] = self.make_entity(
                category, name, {filename: b"semantic\x00content\n"}
            )

        report = migrate_contexts(self.source, self.destination)

        self.assertEqual({item.name for item in report.moved}, {"alex", "server", "rotbot"})
        self.assertFalse(report.conflicted)
        for category, name, filename in records:
            target = self.destination / category / name
            if category == "machines":
                machine_metadata = (target / "metadata.toml").read_text(encoding="utf-8")
                self.assertIn('type = "machine"', machine_metadata)
                self.assertIn(f'name = "{name}"', machine_metadata)
                self.assertIn(f'display_name = "{name.title()}"', machine_metadata)
            else:
                self.assertEqual((target / "metadata.toml").read_bytes(), metadata[name])
            self.assertEqual((target / "private" / filename).read_bytes(), b"semantic\x00content\n")
            if category == "users":
                self.assertFalse((target / "identity.md").exists())
                self.assertTrue((target / "general" / "identity.md").is_file())
            else:
                self.assertEqual(list((target / "general").iterdir()), [])
                self.assertTrue((target / "identity.md").is_file())
            self.assertTrue((target / "relationships.toml").is_file())

    def test_nested_privacy_namespaces_fail_closed(self):
        self.make_entity("projects", "private", {
            "local/notes.md": b"private",
            "shareable/docs/summary.md": b"public",
        })

        report = migrate_contexts(self.source, self.destination)

        self.assertEqual(len(report.conflicted), 1)
        self.assertIn("nested privacy paths", report.conflicted[0].detail)
        self.assertFalse((self.destination / "projects/private").exists())

    def test_assistant_capabilities_remain_structural(self):
        self.make_entity(
            "assistants", "forge", {"capabilities.toml": b"[interaction]\n"}
        )

        report = migrate_contexts(self.source, self.destination)

        self.assertEqual(len(report.moved), 1)
        target = self.destination / "assistants/forge"
        self.assertEqual((target / "capabilities.toml").read_bytes(), b"[interaction]\n")
        self.assertFalse((target / "private/capabilities.toml").exists())

    def test_conflict_is_not_overwritten_and_source_remains(self):
        source, _ = self.make_entity("users", "alex", {"identity.md": b"source"})
        destination = self.destination / "users/alex"
        destination.mkdir(parents=True)
        (destination / "metadata.toml").write_bytes(b"different")

        report = migrate_contexts(self.source, self.destination, delete_source=True)

        self.assertEqual(len(report.conflicted), 1)
        self.assertEqual((destination / "metadata.toml").read_bytes(), b"different")
        self.assertTrue(source.exists())

    def test_identical_destination_is_idempotently_skipped(self):
        self.make_entity("machines", "server", {"identity.md": b"same"})
        first = migrate_contexts(self.source, self.destination)
        second = migrate_contexts(self.source, self.destination)

        self.assertEqual(len(first.moved), 1)
        self.assertEqual(len(second.skipped), 1)
        self.assertEqual(second.skipped[0].classification, "identical")
        self.assertFalse(second.conflicted)

    def test_source_entity_is_deleted_only_when_requested_after_verification(self):
        source, _ = self.make_entity("projects", "rotbot", {"state.md": b"state"})
        category = source.parent
        migrate_contexts(self.source, self.destination)
        self.assertTrue(source.exists())

        report = migrate_contexts(self.source, self.destination, delete_source=True)

        self.assertEqual(len(report.skipped), 1)
        self.assertFalse(source.exists())
        self.assertTrue(category.exists())
        self.assertEqual(
            (self.destination / "projects/rotbot/private/state.md").read_bytes(), b"state"
        )

    def test_repository_builtin_rot_is_skipped(self):
        source, _ = self.make_entity("assistants", "rot")
        report = migrate_contexts(
            self.source, self.destination, repository_source=True
        )
        rot = [item for item in report.skipped if item.name == "rot"]
        self.assertEqual(len(rot), 1)
        self.assertEqual(rot[0].classification, "builtin")
        self.assertFalse((self.destination / "assistants/rot").exists())
        self.assertTrue(source.is_dir())

    def test_legacy_people_roles_map_to_canonical_categories(self):
        for role, category in (
            ("user", "users"), ("assistant", "assistants"), ("contact", "contacts")
        ):
            self.make_entity(f"people/{role}", role, {"identity.md": role.encode()})

        report = migrate_contexts(self.source, self.destination)

        self.assertEqual(len(report.moved), 3)
        for role, category in (
            ("user", "users"), ("assistant", "assistants"), ("contact", "contacts")
        ):
            self.assertEqual(
                (self.destination / category / role / "private/identity.md").read_bytes(),
                role.encode(),
            )

    def test_legacy_namespaces_and_match_document_map_to_canonical_locations(self):
        self.make_entity("projects", "rotbot", {
            "local/notes.md": b"private notes\n",
            "shareable/overview.md": b"general overview\n",
            "local/match.md": (
                b"# Match\n\n## Source\n\nGit remotes:\n"
                b"- github.com/example/rotbot\n\nRequired paths:\n- README.md\n"
            ),
        })

        report = migrate_contexts(self.source, self.destination)

        self.assertEqual(len(report.moved), 1)
        target = self.destination / "projects/rotbot"
        self.assertEqual((target / "private/notes.md").read_bytes(), b"private notes\n")
        self.assertEqual((target / "general/overview.md").read_bytes(), b"general overview\n")
        self.assertTrue((target / "match.toml").is_file())

    def test_rich_markdown_documents_are_preserved_byte_for_byte(self):
        rich = b"# Notes\n\n## Decisions\n\n- Keep details.\n\n```text\nverbatim\n```\n"
        self.make_entity("users", "alex", {"local/notes.md": rich})

        report = migrate_contexts(self.source, self.destination)

        self.assertEqual(len(report.moved), 1)
        self.assertEqual(
            (self.destination / "users/alex/private/notes.md").read_bytes(), rich
        )

    def test_symlinked_source_file_is_rejected(self):
        source, _ = self.make_entity("users", "alex")
        outside = self.source / "outside.md"
        outside.write_bytes(b"secret")
        (source / "identity.md").symlink_to(outside)

        report = migrate_contexts(self.source, self.destination)

        self.assertEqual(len(report.conflicted), 1)
        self.assertFalse((self.destination / "users/alex").exists())


if __name__ == "__main__":
    unittest.main()
