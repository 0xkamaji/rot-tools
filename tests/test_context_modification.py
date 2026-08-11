import argparse
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from rotbot.contexts import loader, modification, people


class PersonModificationTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.context_root = Path(self.temporary_directory.name) / "context"
        self.context_root.mkdir()
        self.people_root = self.context_root / "people"
        self.people_root.mkdir()
        self.projects = self.context_root / "projects"
        self.projects.mkdir()
        self.root_patch = patch.object(loader, "CONTEXT_ROOT", self.context_root)
        self.root_patch.start()

    def tearDown(self):
        self.root_patch.stop()
        self.temporary_directory.cleanup()

    def create_person(self, name="alex", role="contact", display_name="Alex"):
        people.create_person_context(
            name,
            role,
            display_name,
            people_root=self.people_root
        )
        return self.people_root / name

    def test_adds_predefined_category_without_modifying_metadata(self):
        destination = self.create_person()
        metadata = (destination / "metadata.toml").read_text(encoding="utf-8")

        updated = modification.add_person_information(
            "alex",
            "identity.md",
            "Background",
            "Grew up near the coast.",
            people_root=self.people_root
        )

        content = updated.read_text(encoding="utf-8")
        self.assertIn("- Grew up near the coast.\n", content)
        self.assertLess(
            content.index("- Grew up near the coast."),
            content.index("## Skills and Knowledge")
        )
        self.assertEqual(
            (destination / "metadata.toml").read_text(encoding="utf-8"),
            metadata
        )

    def test_existing_category_receives_information_before_next_category(self):
        destination = self.create_person()
        identity = destination / "identity.md"
        identity.write_text(
            "# Identity\n\n## Background\n\n- Existing.\n\n"
            "## Interests\n\n- Music.\n",
            encoding="utf-8"
        )

        modification.add_person_information(
            "alex",
            "identity.md",
            "Background",
            "New detail.",
            people_root=self.people_root
        )

        content = identity.read_text(encoding="utf-8")
        self.assertEqual(content.count("## Background"), 1)
        self.assertLess(content.index("- New detail."), content.index("## Interests"))

    def test_role_controls_available_documents_and_metadata_is_never_available(self):
        self.create_person()
        contact = people.load_person_context("alex", people_root=self.people_root)
        self.create_person("kamaji", "user", "Kamaji")
        user = people.load_person_context("kamaji", people_root=self.people_root)

        self.assertEqual(
            modification.available_documents(contact),
            ("identity.md", "preferences.md", "relationship.md", "state.md")
        )
        self.assertIn("experience.md", modification.available_documents(user))
        self.assertIn("priorities.md", modification.available_documents(user))
        self.assertNotIn("metadata.toml", modification.available_documents(user))
        with self.assertRaises(modification.PersonModificationError):
            modification.add_person_information(
                "alex",
                "experience.md",
                "Professional",
                "Not allowed.",
                people_root=self.people_root
            )

    def test_invalid_input_duplicate_categories_and_symlinks_are_rejected(self):
        destination = self.create_person()
        identity = destination / "identity.md"
        original = "# Identity\n\n## Background\n\n- One.\n\n## Background\n\n- Two.\n"
        identity.write_text(original, encoding="utf-8")
        with self.assertRaises(modification.PersonModificationError):
            modification.add_person_information(
                "alex",
                "identity.md",
                "Background",
                "Three.",
                people_root=self.people_root
            )
        self.assertEqual(identity.read_text(encoding="utf-8"), original)

        for category, information in (("", "detail"), ("Unsafe\nHeading", "detail"), ("Safe", "")):
            with self.subTest(category=category, information=information), self.assertRaises(
                modification.PersonModificationError
            ):
                modification.add_person_information(
                    "alex",
                    "identity.md",
                    category,
                    information,
                    people_root=self.people_root
                )

        identity.unlink()
        outside = Path(self.temporary_directory.name) / "outside.md"
        outside.write_text("outside", encoding="utf-8")
        identity.symlink_to(outside)
        with self.assertRaises(modification.PersonModificationError):
            modification.add_person_information(
                "alex", "identity.md", "Background", "Detail", people_root=self.people_root
            )
        self.assertEqual(outside.read_text(encoding="utf-8"), "outside")

    def test_failed_atomic_replace_preserves_original_and_cleans_temporary_file(self):
        destination = self.create_person()
        identity = destination / "identity.md"
        original = identity.read_text(encoding="utf-8")

        with patch.object(
            modification.os,
            "replace",
            side_effect=OSError("replace failed")
        ), self.assertRaises(modification.PersonModificationError):
            modification.add_person_information(
                "alex",
                "identity.md",
                "Background",
                "Detail.",
                people_root=self.people_root
            )

        self.assertEqual(identity.read_text(encoding="utf-8"), original)
        self.assertEqual(tuple(destination.glob(".identity.md.*.tmp")), ())

    def test_omitted_name_lists_people_and_routes_numbered_choices(self):
        self.create_person("alex", "contact", "Alex")
        self.create_person("zeta", "user", "Zeta")
        answers = ("1", "1", "1", "A detail.", "yes")

        with patch("builtins.input", side_effect=answers), patch.object(
            modification,
            "add_person_information",
            return_value=self.people_root / "alex" / "identity.md"
        ) as add_information, patch.object(
            modification,
            "rot_say"
        ) as rot_say, patch.object(modification, "rot_continue"):
            result = modification.context_mod(argparse.Namespace(name=None))

        self.assertEqual(result, 0)
        add_information.assert_called_once_with(
            "alex",
            "identity.md",
            "Background",
            "A detail.",
            category_description=None
        )
        self.assertTrue(any(
            "1. alex (Alex, contact)" in call.args[0]
            and "2. zeta (Zeta, user)" in call.args[0]
            for call in rot_say.call_args_list
        ))

    def test_explicit_person_can_add_custom_category(self):
        self.create_person()
        categories = modification.person_document_categories(
            "alex", "identity.md", people_root=self.people_root
        )
        custom_option = str(len(categories) + 1)
        answers = (
            "1",
            custom_option,
            "Family",
            "Family members and relevant family context.",
            "Has two siblings.",
            "yes"
        )

        with patch("builtins.input", side_effect=answers), patch.object(
            modification,
            "add_person_information",
            return_value=self.people_root / "alex" / "identity.md"
        ) as add_information, patch.object(
            modification,
            "rot_say"
        ), patch.object(modification, "rot_continue"):
            result = modification.context_mod(argparse.Namespace(name="alex"))

        self.assertEqual(result, 0)
        add_information.assert_called_once_with(
            "alex",
            "identity.md",
            "Family",
            "Has two siblings.",
            category_description="Family members and relevant family context."
        )

    def test_new_category_uses_guidance_template_and_is_discovered_later(self):
        destination = self.create_person()

        modification.add_person_information(
            "alex",
            "identity.md",
            "Family",
            "Has two siblings.",
            category_description="Family members and relevant family context.",
            people_root=self.people_root
        )

        content = (destination / "identity.md").read_text(encoding="utf-8")
        self.assertIn(
            "## Family\n\n"
            "<!-- Family members and relevant family context. -->\n\n"
            "- Has two siblings.\n",
            content
        )
        self.assertEqual(
            modification.person_document_categories(
                "alex", "identity.md", people_root=self.people_root
            )[-1],
            "Family"
        )

    def test_cancel_and_empty_people_modify_nothing(self):
        with patch.object(modification, "rot_say"):
            self.assertEqual(
                modification.context_mod(argparse.Namespace(name=None)),
                1
            )
        destination = self.create_person()
        identity = destination / "identity.md"
        original = identity.read_text(encoding="utf-8")
        with patch("builtins.input", side_effect=("1", EOFError())), patch.object(
            modification,
            "rot_say"
        ):
            result = modification.context_mod(argparse.Namespace(name="alex"))
        self.assertEqual(result, 0)
        self.assertEqual(identity.read_text(encoding="utf-8"), original)


if __name__ == "__main__":
    unittest.main()
