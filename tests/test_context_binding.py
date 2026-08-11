import argparse
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from rotbot.contexts import binding as context_binding
from rotbot.contexts import config as rotbot_config
from rotbot.contexts.matching import Evidence, MatchCandidate


class ContextBindingTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.candidate_path = self.root / "candidate"
        self.candidate_path.mkdir()
        self.config_home = self.root / "config"
        self.environment = patch.dict(
            "os.environ",
            {"XDG_CONFIG_HOME": str(self.config_home)},
            clear=True
        )
        self.environment.start()

    def tearDown(self):
        self.environment.stop()
        self.temporary_directory.cleanup()

    def candidate(self, name="example", binding_type="source", strong=True):
        return MatchCandidate(
            name,
            binding_type,
            self.candidate_path.resolve(),
            strong,
            (Evidence(strong, "matching evidence"),)
        )

    def args(self, first=None, second=None, binding_type=None):
        return argparse.Namespace(
            first=first,
            second=second,
            binding_type=binding_type
        )

    def test_declined_confirmation_does_not_create_configuration(self):
        with patch.object(
            context_binding,
            "match_contexts",
            return_value=(self.candidate(),)
        ), patch("builtins.input", return_value="n"), patch.object(
            context_binding,
            "rot_say"
        ), patch.object(context_binding, "rot_continue"):
            result = context_binding.context_bind(self.args(str(self.candidate_path)))

        self.assertEqual(result, 0)
        self.assertFalse(rotbot_config.config_path().exists())

    def test_confirmed_binding_saves_resolved_path(self):
        with patch.object(
            context_binding,
            "match_contexts",
            return_value=(self.candidate(),)
        ), patch("builtins.input", return_value="yes"), patch.object(
            context_binding,
            "rot_say"
        ), patch.object(context_binding, "rot_continue"):
            result = context_binding.context_bind(self.args(str(self.candidate_path)))

        self.assertEqual(result, 0)
        self.assertEqual(
            rotbot_config.get_context_binding("example")["source_path"],
            str(self.candidate_path.resolve())
        )
        config_text = rotbot_config.config_path().read_text(encoding="utf-8")
        self.assertIn("[contexts.example]", config_text)
        self.assertNotIn("contexts.projects", config_text)

    def test_failed_and_ambiguous_matches_do_not_write(self):
        cases = (
            (self.candidate(strong=False),),
            (self.candidate("first"), self.candidate("second"))
        )
        for candidates in cases:
            with self.subTest(candidates=candidates), patch.object(
                context_binding,
                "match_contexts",
                return_value=candidates
            ), patch("builtins.input") as user_input, patch.object(
                context_binding,
                "rot_say"
            ), patch.object(context_binding, "rot_continue"):
                result = context_binding.context_bind(self.args(str(self.candidate_path)))

            self.assertEqual(result, 1)
            user_input.assert_not_called()
            self.assertFalse(rotbot_config.config_path().exists())

    def test_malformed_existing_configuration_blocks_confirmation(self):
        config = rotbot_config.config_path()
        config.parent.mkdir(parents=True)
        config.write_text("[broken", encoding="utf-8")

        with patch.object(
            context_binding,
            "match_contexts",
            return_value=(self.candidate(),)
        ), patch("builtins.input") as user_input, patch.object(
            context_binding,
            "rot_say"
        ), patch.object(context_binding, "rot_continue"):
            result = context_binding.context_bind(self.args(str(self.candidate_path)))

        self.assertEqual(result, 1)
        user_input.assert_not_called()
        self.assertEqual(config.read_text(encoding="utf-8"), "[broken")


if __name__ == "__main__":
    unittest.main()
