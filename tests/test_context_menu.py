import argparse
import unittest
from unittest.mock import patch

from rotbot.contexts import menu


class ContextMenuTests(unittest.TestCase):
    def test_number_and_action_name_route_to_existing_handlers(self):
        cases = (
            ("1", "context_add", {"agent": None}),
            ("dev", "context_develop", {"name": None, "agent": None}),
            ("list", "context_list", {}),
            ("m", "context_mod", {"name": None}),
            ("5", "context_show", {"name": None, "vision": False}),
            (
                "bind",
                "context_bind",
                {"first": None, "second": None, "binding_type": None}
            ),
            ("archive", "context_delete", {"name": None})
        )
        for answer, handler_name, expected in cases:
            with self.subTest(answer=answer), patch(
                "builtins.input",
                return_value=answer
            ), patch.object(menu, handler_name, return_value=7) as handler, patch.object(
                menu,
                "rot_say"
            ):
                result = menu.context_menu(argparse.Namespace())

            self.assertEqual(result, 7)
            arguments = handler.call_args.args[0]
            for name, value in expected.items():
                self.assertEqual(getattr(arguments, name), value)

    def test_menu_displays_descriptions_and_retries_invalid_choice(self):
        with patch("builtins.input", side_effect=("invalid", "list")), patch.object(
            menu,
            "context_list",
            return_value=0
        ), patch.object(menu, "rot_say") as rot_say:
            result = menu.context_menu(argparse.Namespace())

        self.assertEqual(result, 0)
        menu_text = rot_say.call_args_list[0].args[0]
        for name, description in menu.ACTIONS:
            self.assertIn(name, menu_text)
            self.assertIn(description, menu_text)
        self.assertIn("enter an action name", rot_say.call_args_list[1].args[0])

    def test_blank_exit_and_eof_close_without_calling_handlers(self):
        for side_effect in (("",), ("8",), (EOFError(),)):
            with self.subTest(side_effect=side_effect), patch(
                "builtins.input",
                side_effect=side_effect
            ), patch.object(menu, "context_list") as context_list, patch.object(
                menu,
                "rot_say"
            ):
                result = menu.context_menu(argparse.Namespace())

            self.assertEqual(result, 0)
            context_list.assert_not_called()


if __name__ == "__main__":
    unittest.main()
