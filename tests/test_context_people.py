from pathlib import Path
import tempfile
import tomllib
import unittest
from unittest.mock import patch

from rotbot.contexts import people


class PersonContextTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name) / "people"
        self.root.mkdir()
        for role in people.PERSON_ROLES:
            (self.root / role).mkdir()

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_contact_creation_has_canonical_layout_and_metadata(self):
        destination = people.create_person_context(
            "alex", "contact", "Alex Example", people_root=self.root
        )

        self.assertEqual(
            {path.name for path in destination.iterdir()},
            {
                "metadata.toml",
                "identity.md",
                "relationships.toml",
                "general",
                "private"
            }
        )
        metadata = tomllib.loads(
            (destination / "metadata.toml").read_text(encoding="utf-8")
        )
        self.assertEqual(metadata["type"], "person")
        self.assertEqual(metadata["role"], "contact")
        self.assertEqual(metadata["name"], "alex")
        self.assertEqual(metadata["display_name"], "Alex Example")
        self.assertEqual(metadata["related_projects"], [])
        self.assertRegex(metadata["id"], r"^[0-9a-f-]{36}$")

    def test_user_creation_uses_same_empty_dynamic_namespaces(self):
        destination = people.create_person_context(
            "kamaji", "user", people_root=self.root
        )

        self.assertEqual(
            {path.name for path in destination.iterdir()},
            {
                "metadata.toml",
                "identity.md",
                "relationships.toml",
                "general",
                "private"
            }
        )
        metadata = tomllib.loads(
            (destination / "metadata.toml").read_text(encoding="utf-8")
        )
        self.assertEqual(metadata["role"], "user")
        self.assertEqual(metadata["name"], "kamaji")
        self.assertRegex(metadata["id"], r"^[0-9a-f-]{36}$")
        self.assertEqual(tuple((destination / "general").iterdir()), ())
        self.assertEqual(tuple((destination / "private").iterdir()), ())

    def test_assistant_creation_uses_core_layout_and_related_project(self):
        destination = people.create_person_context(
            "rot",
            "assistant",
            "Rot",
            ["rotbot"],
            people_root=self.root
        )

        self.assertEqual(
            {path.name for path in destination.iterdir()},
            {
                "metadata.toml",
                "identity.md",
                "relationships.toml",
                "general",
                "private"
            }
        )
        metadata = tomllib.loads(
            (destination / "metadata.toml").read_text(encoding="utf-8")
        )
        self.assertEqual(metadata["role"], "assistant")
        self.assertEqual(metadata["name"], "rot")
        self.assertEqual(metadata["related_projects"], ["rotbot"])
        self.assertRegex(metadata["id"], r"^[0-9a-f-]{36}$")
        loaded = people.load_person_context("rot", people_root=self.root)
        self.assertEqual(loaded.role, "assistant")
        self.assertEqual(loaded.related_projects, ("rotbot",))

    def test_related_projects_preserve_order_and_remove_duplicates(self):
        person = people.build_person_context(
            "alex",
            "contact",
            "Alex",
            ["rotbot", "signalrot", "rotbot", "signalrot"]
        )

        self.assertEqual(person.related_projects, ("rotbot", "signalrot"))
        self.assertIn(
            'related_projects = ["rotbot", "signalrot"]\n',
            people.render_person_files(person)["metadata.toml"]
        )

    def test_related_projects_do_not_need_to_exist(self):
        destination = people.create_person_context(
            "future-user",
            "user",
            related_projects=["future-project"],
            people_root=self.root
        )

        self.assertTrue(destination.is_dir())
        self.assertIn(
            'related_projects = ["future-project"]',
            (destination / "metadata.toml").read_text(encoding="utf-8")
        )

    def test_invalid_related_projects_are_rejected_before_creation(self):
        invalid_values = (
            "rotbot",
            7,
            [""],
            ["../rotbot"],
            ["projects/rotbot"],
            ["nested\\rotbot"],
            [None],
            [42]
        )
        for index, related_projects in enumerate(invalid_values):
            name = f"invalid-{index}"
            with self.subTest(related_projects=related_projects), self.assertRaises(
                people.PersonContextError
            ):
                people.create_person_context(
                    name,
                    "contact",
                    related_projects=related_projects,
                    people_root=self.root
                )
            self.assertFalse(any(
                (self.root / role / name).exists()
                for role in people.PERSON_ROLES
            ))

    def test_legacy_metadata_without_related_projects_loads_as_empty(self):
        destination = people.create_person_context(
            "legacy", "contact", people_root=self.root
        )
        metadata = destination / "metadata.toml"
        metadata.write_text(
            metadata.read_text(encoding="utf-8").replace(
                "related_projects = []\n",
                ""
            ),
            encoding="utf-8"
        )

        loaded = people.load_person_context("legacy", people_root=self.root)

        self.assertEqual(loaded.related_projects, ())

    def test_related_projects_metadata_must_be_a_toml_array(self):
        destination = people.create_person_context(
            "invalid-metadata", "contact", people_root=self.root
        )
        metadata = destination / "metadata.toml"
        metadata.write_text(
            metadata.read_text(encoding="utf-8").replace(
                "related_projects = []",
                'related_projects = "rotbot"'
            ),
            encoding="utf-8"
        )

        with self.assertRaisesRegex(
            people.PersonContextError,
            "Invalid related projects metadata"
        ):
            people.load_person_context("invalid-metadata", people_root=self.root)

    def test_person_loading_and_listing_uses_valid_metadata(self):
        people.create_person_context(
            "zeta", "contact", "Zeta Person", people_root=self.root
        )
        people.create_person_context(
            "alpha", "user", "Alpha Person", people_root=self.root
        )

        loaded = people.load_person_context("alpha", people_root=self.root)

        self.assertEqual(
            (loaded.name, loaded.role, loaded.display_name, loaded.related_projects),
            ("alpha", "user", "Alpha Person", ())
        )
        self.assertIsNotNone(loaded.id)

    def test_person_context_can_be_loaded_by_stable_id(self):
        people.create_person_context("alex", "user", people_root=self.root)
        person = people.load_person_context("alex", people_root=self.root)

        self.assertEqual(
            people.load_person_context_reference(
                person.id,
                "user",
                people_root=self.root
            ),
            person
        )

    def test_person_documents_return_only_populated_content(self):
        destination = people.create_person_context(
            "alex", "contact", "Alex", people_root=self.root
        )
        profile = destination / "general" / "profile.md"
        profile.write_text(
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
        identity_document, profile_document = documents
        self.assertEqual(identity_document.filename, "identity.md")
        self.assertEqual(profile_document.filename, "profile.md")
        self.assertEqual(
            profile_document.sections,
            (("Background", "- Grew up near the coast."),)
        )
        self.assertTrue(identity_document.sections)

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
        invalid = self.root / "contact" / "invalid"
        invalid.mkdir()
        (invalid / "metadata.toml").write_text(
            'type = "project"\nrole = "contact"\nname = "invalid"\n'
            'display_name = "Invalid"\n',
            encoding="utf-8"
        )
        mismatch = self.root / "contact" / "mismatch"
        mismatch.mkdir()
        (mismatch / "metadata.toml").write_text(
            'type = "person"\nrole = "contact"\nname = "other"\n'
            'display_name = "Other"\n',
            encoding="utf-8"
        )
        outside = Path(self.temporary_directory.name) / "outside"
        outside.mkdir()
        (self.root / "contact" / "linked").symlink_to(
            outside,
            target_is_directory=True
        )

        self.assertEqual(
            tuple(person.name for person in people.list_person_contexts(people_root=self.root)),
            ("valid",)
        )
        for name in ("invalid", "mismatch", "linked", "../outside"):
            with self.subTest(name=name), self.assertRaises(people.PersonContextError):
                people.load_person_context(name, people_root=self.root)

    def test_rendered_person_files_are_only_canonical_structural_documents(self):
        files = people.render_person_files(
            people.build_person_context("kamaji", "user")
        )

        self.assertEqual(
            set(files), {"metadata.toml", "identity.md", "relationships.toml"}
        )
        self.assertIn("# kamaji", files["identity.md"])
        self.assertEqual(tomllib.loads(files["relationships.toml"]), {})

    def test_unsupported_role_is_rejected_before_creation(self):
        with self.assertRaises(people.PersonContextError):
            people.create_person_context("alex", "admin", people_root=self.root)

        self.assertEqual(
            {path.name for path in self.root.iterdir()},
            set(people.PERSON_ROLES)
        )

    def test_unsafe_and_invalid_names_are_rejected(self):
        for name in (None, "", "../alex", "nested/alex", ".hidden", "/tmp/alex"):
            with self.subTest(name=name), self.assertRaises(people.PersonContextError):
                people.create_person_context(name, "contact", people_root=self.root)
        self.assertEqual(
            {path.name for path in self.root.iterdir()},
            set(people.PERSON_ROLES)
        )

    def test_existing_target_is_not_modified(self):
        destination = self.root / "contact" / "alex"
        destination.mkdir()
        marker = destination / "marker.txt"
        marker.write_text("unchanged", encoding="utf-8")

        with self.assertRaises(people.PersonContextError):
            people.create_person_context("alex", "contact", people_root=self.root)

        self.assertEqual(marker.read_text(encoding="utf-8"), "unchanged")
        self.assertEqual({path.name for path in destination.iterdir()}, {"marker.txt"})

    def test_name_is_unique_across_roles_and_role_directory_must_match_metadata(self):
        destination = people.create_person_context(
            "alex", "contact", people_root=self.root
        )
        with self.assertRaises(people.PersonContextError):
            people.create_person_context("alex", "assistant", people_root=self.root)
        self.assertFalse((self.root / "assistant" / "alex").exists())

        moved = self.root / "assistant" / "alex"
        destination.rename(moved)
        with self.assertRaisesRegex(people.PersonContextError, "does not match"):
            people.load_person_context("alex", people_root=self.root)

    def test_duplicate_name_across_role_directories_is_rejected(self):
        people.create_person_context("alex", "contact", people_root=self.root)
        duplicate = self.root / "assistant" / "alex"
        duplicate.mkdir()

        with self.assertRaisesRegex(people.PersonContextError, "multiple role"):
            people.load_person_context("alex", people_root=self.root)
        with self.assertRaisesRegex(people.PersonContextError, "multiple role"):
            people.list_person_contexts(people_root=self.root)

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

        self.assertFalse((self.root / "contact" / "alex").exists())


if __name__ == "__main__":
    unittest.main()
