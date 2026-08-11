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

    def test_person_loading_and_listing_uses_valid_metadata(self):
        people.create_person_context(
            "zeta", "contact", "Zeta Person", people_root=self.root
        )
        people.create_person_context(
            "alpha", "user", "Alpha Person", people_root=self.root
        )

        loaded = people.load_person_context("alpha", people_root=self.root)

        self.assertEqual(
            loaded,
            people.PersonContext("alpha", "user", "Alpha Person")
        )
        self.assertEqual(
            tuple(person.name for person in people.list_person_contexts(people_root=self.root)),
            ("alpha", "zeta")
        )

    def test_person_documents_return_only_populated_content(self):
        destination = people.create_person_context(
            "alex", "contact", "Alex", people_root=self.root
        )
        identity = destination / "identity.md"
        identity.write_text(
            "# Identity\n\n"
            "<!-- General guidance. -->\n\n"
            "## Background\n\n"
            "<!-- Hidden guidance. -->\n\n"
            "- Grew up near the coast.\n\n"
            "## Interests\n\n"
            "<!-- Empty section. -->\n",
            encoding="utf-8"
        )

        person, documents = people.load_person_documents(
            "alex", people_root=self.root
        )

        self.assertEqual(person.name, "alex")
        identity_document = documents[0]
        self.assertEqual(identity_document.filename, "identity.md")
        self.assertEqual(
            identity_document.sections,
            (("Background", "- Grew up near the coast."),)
        )
        self.assertTrue(all(
            not document.sections for document in documents[1:]
        ))

    def test_person_document_parser_preserves_fenced_content_and_rejects_malformed(self):
        destination = people.create_person_context(
            "alex", "contact", "Alex", people_root=self.root
        )
        identity = destination / "identity.md"
        identity.write_text(
            "# Identity\n\n## Technical Notes\n\n```text\n"
            "## Not a category\n<!-- visible code -->\n```\n",
            encoding="utf-8"
        )
        _person, documents = people.load_person_documents(
            "alex", people_root=self.root
        )
        self.assertIn("## Not a category", documents[0].sections[0][1])
        self.assertIn("<!-- visible code -->", documents[0].sections[0][1])

        identity.write_text(
            "# Identity\n\n## Broken\n\n<!-- unfinished",
            encoding="utf-8"
        )
        with self.assertRaisesRegex(people.PersonContextError, "Unterminated"):
            people.load_person_documents("alex", people_root=self.root)

    def test_person_listing_ignores_invalid_or_unsafe_entries(self):
        people.create_person_context("valid", "contact", people_root=self.root)
        invalid = self.root / "invalid"
        invalid.mkdir()
        (invalid / "metadata.toml").write_text(
            'type = "project"\nrole = "contact"\nname = "invalid"\n'
            'display_name = "Invalid"\n',
            encoding="utf-8"
        )
        mismatch = self.root / "mismatch"
        mismatch.mkdir()
        (mismatch / "metadata.toml").write_text(
            'type = "person"\nrole = "contact"\nname = "other"\n'
            'display_name = "Other"\n',
            encoding="utf-8"
        )
        outside = Path(self.temporary_directory.name) / "outside"
        outside.mkdir()
        (self.root / "linked").symlink_to(outside, target_is_directory=True)

        self.assertEqual(
            tuple(person.name for person in people.list_person_contexts(people_root=self.root)),
            ("valid",)
        )
        for name in ("invalid", "mismatch", "linked", "../outside"):
            with self.subTest(name=name), self.assertRaises(people.PersonContextError):
                people.load_person_context(name, people_root=self.root)

    def test_templates_contain_only_headings_and_guidance(self):
        expected_categories = {
            "identity.md": (
                "Background", "Skills and Knowledge", "Interests", "Traits",
                "Important Details", "Other"
            ),
            "preferences.md": (
                "Communication", "Collaboration", "Tools and Workflows",
                "Likes and Dislikes", "Accessibility and Accommodations", "Other"
            ),
            "relationship.md": (
                "Connection", "Shared History", "Personal Dynamic",
                "Working Dynamic", "Shared Responsibilities", "Boundaries", "Other"
            ),
            "state.md": (
                "Current Circumstances", "Active Work", "Upcoming", "Open Items",
                "Recent Changes", "Other"
            ),
            "experience.md": (
                "Professional", "Technical", "Creative", "Practical",
                "Education and Training", "Learning", "Other"
            ),
            "priorities.md": (
                "Current Goals", "Ongoing Responsibilities", "Areas of Focus",
                "Constraints", "Later", "Other"
            )
        }
        files = people.render_person_files(
            people.build_person_context("kamaji", "user")
        )

        for filename, categories in expected_categories.items():
            with self.subTest(filename=filename):
                content = files[filename]
                headings = tuple(
                    line[3:] for line in content.splitlines() if line.startswith("## ")
                )
                self.assertEqual(headings, categories)
                self.assertEqual(content.count("<!--"), len(categories) + 1)
                self.assertNotIn("\n- ", content)

        self.assertIn(
            "<!-- Accessibility needs, accommodations, or circumstances that "
            "should influence interactions and planning. -->",
            files["preferences.md"]
        )
        self.assertIn(
            "<!-- Time, money, health, technical, logistical, or situational "
            "limitations that may affect recommendations and plans. -->",
            files["priorities.md"]
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
