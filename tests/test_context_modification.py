import argparse
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from rotbot.contexts import entities, loader, modification, people


class PersonModificationTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.context_root = Path(self.temporary_directory.name) / "context"
        self.context_root.mkdir()
        self.contacts = self.context_root / "contacts"
        self.contacts.mkdir()
        self.users = self.context_root / "users"
        self.users.mkdir()
        self.assistants = self.context_root / "assistants"
        self.assistants.mkdir()
        self.projects = self.context_root / "projects"
        self.projects.mkdir()
        self.root_patch = patch.object(loader, "CONTEXT_ROOT", self.context_root)
        self.root_patch.start()
        self.builtins_patch = patch.object(
            entities,
            "builtin_assistants_root",
            return_value=self.context_root / ".builtins" / "assistants"
        )
        self.builtins_patch.start()

    def tearDown(self):
        self.builtins_patch.stop()
        self.root_patch.stop()
        self.temporary_directory.cleanup()

    def create_person(
        self,
        name="alex",
        role="contact",
        display_name="Alex",
        related_projects=None
    ):
        if role == "contact":
            return people.create_person_context(
                name, role, display_name, related_projects
            )
        entity = (
            entities.build_user_context(name, display_name, related_projects)
            if role == "user"
            else entities.build_assistant_context(name, display_name, related_projects)
        )
        return entities.create_entity_context(entity, root=self.context_root)

    def create_project(self, name):
        destination = self.projects / name
        destination.mkdir()
        (destination / "metadata.toml").write_text(
            loader.render_project_metadata(name), encoding="utf-8"
        )
        (destination / "general").mkdir()
        (destination / "private").mkdir()
        (destination / "identity.md").write_text("identity", encoding="utf-8")
        (destination / "relationships.toml").write_text("", encoding="utf-8")
        return destination

    def test_adds_category_to_dynamic_private_document_without_modifying_metadata(self):
        destination = self.create_person()
        metadata = (destination / "metadata.toml").read_text(encoding="utf-8")

        updated = modification.add_person_information(
            "alex",
            "biography.md",
            "Background",
            "Grew up near the coast.",
            category_description="Personal history and relevant background."
        )

        content = updated.read_text(encoding="utf-8")
        self.assertIn("- Grew up near the coast.\n", content)
        self.assertEqual(
            (destination / "metadata.toml").read_text(encoding="utf-8"),
            metadata
        )

    def test_existing_category_receives_information_before_next_category(self):
        destination = self.create_person()
        identity = destination / "private" / "biography.md"
        identity.write_text(
            "# Identity\n\n## Background\n\n- Existing.\n\n"
            "## Interests\n\n- Music.\n",
            encoding="utf-8"
        )

        modification.add_person_information(
            "alex",
            "biography.md",
            "Background",
            "New detail."
        )

        content = identity.read_text(encoding="utf-8")
        self.assertEqual(content.count("## Background"), 1)
        self.assertLess(content.index("- New detail."), content.index("## Interests"))

    def test_available_documents_are_discovered_dynamically(self):
        contact_path = self.create_person()
        (contact_path / "private" / "notes.md").write_text("# Notes\n", encoding="utf-8")
        contact = people.load_person_context("alex")
        user_path = self.create_person("kamaji", "user", "Kamaji")
        (user_path / "private" / "experience.md").write_text("# Experience\n", encoding="utf-8")
        user = entities.load_user_context("kamaji", root=self.context_root)
        assistant_path = self.create_person("rot", "assistant", "Rot")
        (assistant_path / "private" / "journal.md").write_text("# Journal\n", encoding="utf-8")
        assistant = entities.load_assistant_context("rot", root=self.context_root)

        self.assertEqual(modification.available_documents(contact), ("notes.md",))
        self.assertEqual(modification.available_documents(user), ("experience.md",))
        self.assertEqual(modification.available_documents(assistant), ("journal.md",))

    def test_invalid_input_duplicate_categories_and_symlinks_are_rejected(self):
        destination = self.create_person()
        identity = destination / "private" / "biography.md"
        identity.write_text("# Biography\n", encoding="utf-8")
        original = "# Identity\n\n## Background\n\n- One.\n\n## Background\n\n- Two.\n"
        identity.write_text(original, encoding="utf-8")
        with self.assertRaises(modification.PersonModificationError):
            modification.add_person_information(
                "alex",
                "biography.md",
                "Background",
                "Three."
            )
        self.assertEqual(identity.read_text(encoding="utf-8"), original)

        for category, information in (("", "detail"), ("Unsafe\nHeading", "detail"), ("Safe", "")):
            with self.subTest(category=category, information=information), self.assertRaises(
                modification.PersonModificationError
            ):
                modification.add_person_information(
                    "alex",
                    "biography.md",
                    category,
                    information
                )

        identity.unlink()
        outside = Path(self.temporary_directory.name) / "outside.md"
        outside.write_text("outside", encoding="utf-8")
        identity.symlink_to(outside)
        with self.assertRaises(modification.PersonModificationError):
            modification.add_person_information(
                "alex", "biography.md", "Background", "Detail"
            )
        self.assertEqual(outside.read_text(encoding="utf-8"), "outside")

    def test_failed_atomic_replace_preserves_original_and_cleans_temporary_file(self):
        destination = self.create_person()
        identity = destination / "private" / "biography.md"
        identity.write_text("# Biography\n", encoding="utf-8")
        original = identity.read_text(encoding="utf-8")

        with patch.object(
            modification.os,
            "replace",
            side_effect=OSError("replace failed")
        ), self.assertRaises(modification.PersonModificationError):
            modification.add_person_information(
                "alex",
                "biography.md",
                "Background",
                "Detail."
            )

        self.assertEqual(identity.read_text(encoding="utf-8"), original)
        self.assertEqual(tuple((destination / "private").glob(".biography.md.*.tmp")), ())

    def test_metadata_update_changes_only_allowed_fields(self):
        destination = self.create_person(
            related_projects=("rotbot",)
        )
        identity = (destination / "identity.md").read_text(encoding="utf-8")
        original_id = people.load_person_context("alex").id

        modification.replace_person_metadata(
            "alex",
            "Alex Updated",
            ("rotbot", "signalrot")
        )

        loaded = people.load_person_context("alex")
        self.assertEqual(loaded.name, "alex")
        self.assertEqual(loaded.role, "contact")
        self.assertEqual(loaded.display_name, "Alex Updated")
        self.assertEqual(loaded.related_projects, ("rotbot", "signalrot"))
        self.assertEqual(loaded.id, original_id)
        self.assertEqual(
            (destination / "identity.md").read_text(encoding="utf-8"),
            identity
        )

    def test_invalid_metadata_update_preserves_original(self):
        destination = self.create_person(related_projects=("rotbot",))
        metadata = destination / "metadata.toml"
        original = metadata.read_text(encoding="utf-8")

        for display_name, related_projects in (
            ("", ("rotbot",)),
            ("Alex", ("../unsafe",)),
            ("Alex", (42,))
        ):
            with self.subTest(
                display_name=display_name,
                related_projects=related_projects
            ), self.assertRaises(modification.PersonModificationError):
                modification.replace_person_metadata(
                    "alex",
                    display_name,
                    related_projects
                )
            self.assertEqual(metadata.read_text(encoding="utf-8"), original)

    def test_metadata_menu_changes_display_name(self):
        self.create_person()
        answers = ("2", "1", "Alex Updated", "yes")

        with patch("builtins.input", side_effect=answers), patch.object(
            modification,
            "rot_say"
        ), patch.object(modification, "rot_continue"):
            result = modification.context_mod(argparse.Namespace(name="alex"))

        self.assertEqual(result, 0)
        loaded = people.load_person_context("alex")
        self.assertEqual(loaded.display_name, "Alex Updated")

    def test_metadata_menu_adds_existing_project(self):
        self.create_person(related_projects=("rotbot",))
        self.create_project("rotbot")
        self.create_project("signalrot")
        answers = ("2", "2", "1", "yes")

        with patch("builtins.input", side_effect=answers), patch.object(
            modification,
            "rot_say"
        ) as rot_say, patch.object(modification, "rot_continue"):
            result = modification.context_mod(argparse.Namespace(name="alex"))

        self.assertEqual(result, 0)
        loaded = people.load_person_context("alex")
        self.assertEqual(loaded.related_projects, ("rotbot", "signalrot"))
        self.assertTrue(any(
            "signalrot" in call.args[0] and "rotbot" not in call.args[0]
            for call in rot_say.call_args_list
        ))

    def test_metadata_menu_removes_related_project(self):
        self.create_person(related_projects=("rotbot", "signalrot"))
        answers = ("2", "3", "1", "yes")

        with patch("builtins.input", side_effect=answers), patch.object(
            modification,
            "rot_say"
        ), patch.object(modification, "rot_continue"):
            result = modification.context_mod(argparse.Namespace(name="alex"))

        self.assertEqual(result, 0)
        loaded = people.load_person_context("alex")
        self.assertEqual(loaded.related_projects, ("signalrot",))

    def test_metadata_submenu_can_exit_without_changes(self):
        destination = self.create_person(related_projects=("rotbot",))
        metadata = destination / "metadata.toml"
        original = metadata.read_text(encoding="utf-8")

        with patch("builtins.input", side_effect=("2", "4")), patch.object(
            modification,
            "rot_say"
        ):
            result = modification.context_mod(argparse.Namespace(name="alex"))

        self.assertEqual(result, 0)
        self.assertEqual(metadata.read_text(encoding="utf-8"), original)

    def test_failed_metadata_replace_preserves_original_and_cleans_temp(self):
        destination = self.create_person()
        metadata = destination / "metadata.toml"
        original = metadata.read_text(encoding="utf-8")

        with patch.object(
            modification.os,
            "replace",
            side_effect=OSError("replace failed")
        ), self.assertRaises(modification.PersonModificationError):
            modification.replace_person_metadata(
                "alex",
                "Alex Updated",
                ()
            )

        self.assertEqual(metadata.read_text(encoding="utf-8"), original)
        self.assertEqual(tuple(destination.glob(".metadata.toml.*.tmp")), ())

    def test_omitted_name_lists_people_and_routes_numbered_choices(self):
        alex = self.create_person("alex", "contact", "Alex")
        (alex / "private" / "notes.md").write_text(
            "# Notes\n\n## Background\n", encoding="utf-8"
        )
        self.create_person("zeta", "contact", "Zeta")
        answers = ("1", "1", "1", "A detail.", "yes")

        with patch("builtins.input", side_effect=answers), patch.object(
            modification,
            "add_person_information",
            return_value=self.contacts / "alex" / "private" / "notes.md"
        ) as add_information, patch.object(
            modification,
            "rot_say"
        ) as rot_say, patch.object(modification, "rot_continue"):
            result = modification.context_mod(argparse.Namespace(name=None))

        self.assertEqual(result, 0)
        add_information.assert_called_once_with(
            "alex",
            "notes.md",
            "Background",
            "A detail.",
            category_description=None
        )
        self.assertTrue(any(
            "1. alex (Alex, contact)" in call.args[0]
            and "2. zeta (Zeta, contact)" in call.args[0]
            for call in rot_say.call_args_list
        ))

    def test_explicit_person_can_add_custom_category(self):
        self.create_person()
        notes = self.contacts / "alex" / "private" / "notes.md"
        notes.write_text("# Notes\n\n## Background\n", encoding="utf-8")
        categories = modification.person_document_categories(
            "alex", "notes.md"
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
            return_value=self.contacts / "alex" / "private" / "notes.md"
        ) as add_information, patch.object(
            modification,
            "rot_say"
        ), patch.object(modification, "rot_continue"):
            result = modification.context_mod(argparse.Namespace(name="alex"))

        self.assertEqual(result, 0)
        add_information.assert_called_once_with(
            "alex",
            "notes.md",
            "Family",
            "Has two siblings.",
            category_description="Family members and relevant family context."
        )

    def test_new_category_uses_guidance_template_and_is_discovered_later(self):
        destination = self.create_person()

        modification.add_person_information(
            "alex",
            "family.md",
            "Family",
            "Has two siblings.",
            category_description="Family members and relevant family context."
        )

        content = (destination / "private" / "family.md").read_text(encoding="utf-8")
        self.assertIn(
            "## Family\n\n"
            "<!-- Family members and relevant family context. -->\n\n"
            "- Has two siblings.\n",
            content
        )
        self.assertEqual(
            modification.person_document_categories(
                "alex", "family.md"
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

    def test_numbered_modification_menus_offer_graceful_exit(self):
        self.create_person()
        for answer in ("exit", "3"):
            with self.subTest(answer=answer), patch(
                "builtins.input",
                return_value=answer
            ), patch.object(
                modification,
                "add_person_information"
            ) as add_information, patch.object(modification, "rot_say"):
                result = modification.context_mod(argparse.Namespace(name="alex"))

            self.assertEqual(result, 0)
            add_information.assert_not_called()


if __name__ == "__main__":
    unittest.main()
