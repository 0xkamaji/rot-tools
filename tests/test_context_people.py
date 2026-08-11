from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from rotbot.contexts import people


class PersonContextTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name) / "people"
        self.root.mkdir()

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_contact_creation_has_exact_core_layout_and_metadata(self):
        destination = people.create_person_context(
            "alex", "contact", "Alex Example", people_root=self.root
        )

        self.assertEqual(
            {path.name for path in destination.iterdir()},
            {
                "metadata.toml",
                "identity.md",
                "preferences.md",
                "relationship.md",
                "state.md"
            }
        )
        self.assertEqual(
            (destination / "metadata.toml").read_text(encoding="utf-8"),
            'type = "person"\n'
            'role = "contact"\n'
            'name = "alex"\n'
            'display_name = "Alex Example"\n'
        )

    def test_user_creation_adds_user_templates_and_defaults_display_name(self):
        destination = people.create_person_context(
            "kamaji", "user", people_root=self.root
        )

        self.assertEqual(
            {path.name for path in destination.iterdir()},
            {
                "metadata.toml",
                "identity.md",
                "preferences.md",
                "relationship.md",
                "state.md",
                "experience.md",
                "priorities.md"
            }
        )
        self.assertEqual(
            (destination / "metadata.toml").read_text(encoding="utf-8"),
            'type = "person"\n'
            'role = "user"\n'
            'name = "kamaji"\n'
            'display_name = "kamaji"\n'
        )

    def test_templates_contain_only_headings_and_guidance(self):
        contact = people.build_person_context("alex", "contact")
        files = people.render_person_files(contact)

        for filename, content in files.items():
            if filename == "metadata.toml":
                continue
            with self.subTest(filename=filename):
                self.assertTrue(content.startswith("# "))
                self.assertIn("<!--", content)
                self.assertIn("-->", content)
                self.assertEqual(len(content.strip().splitlines()), 3)

        user_files = people.render_person_files(
            people.build_person_context("kamaji", "user")
        )
        self.assertEqual(
            user_files["experience.md"],
            "# Experience\n\n"
            "<!-- Skills, background, knowledge, and capabilities relevant to RotBot. -->\n"
        )
        self.assertEqual(
            user_files["priorities.md"],
            "# Priorities\n\n"
            "<!-- Current goals, responsibilities, and areas of focus. -->\n"
        )

    def test_unsupported_role_is_rejected_before_creation(self):
        with self.assertRaises(people.PersonContextError):
            people.create_person_context("alex", "admin", people_root=self.root)

        self.assertEqual(tuple(self.root.iterdir()), ())

    def test_unsafe_and_invalid_names_are_rejected(self):
        for name in (None, "", "../alex", "nested/alex", ".hidden", "/tmp/alex"):
            with self.subTest(name=name), self.assertRaises(people.PersonContextError):
                people.create_person_context(name, "contact", people_root=self.root)
        self.assertEqual(tuple(self.root.iterdir()), ())

    def test_existing_target_is_not_modified(self):
        destination = self.root / "alex"
        destination.mkdir()
        marker = destination / "marker.txt"
        marker.write_text("unchanged", encoding="utf-8")

        with self.assertRaises(people.PersonContextError):
            people.create_person_context("alex", "contact", people_root=self.root)

        self.assertEqual(marker.read_text(encoding="utf-8"), "unchanged")
        self.assertEqual({path.name for path in destination.iterdir()}, {"marker.txt"})

    def test_write_failure_removes_partial_person_directory(self):
        original_write = people._write_document
        writes = 0

        def fail_second_write(path, content):
            nonlocal writes
            writes += 1
            if writes == 2:
                raise OSError("write failed")
            original_write(path, content)

        with patch.object(people, "_write_document", side_effect=fail_second_write):
            with self.assertRaises(people.PersonContextError):
                people.create_person_context("alex", "contact", people_root=self.root)

        self.assertFalse((self.root / "alex").exists())


if __name__ == "__main__":
    unittest.main()
