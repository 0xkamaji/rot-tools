import argparse
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from rotbot.contexts import config as rotbot_config
from rotbot.integrations.signalrot import commands as signalrot
from rotbot.integrations.signalrot import paths as signalrot_paths


class SignalRotBindingTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.config_home = self.root / "config"
        self.environment = patch.dict(
            os.environ,
            {"XDG_CONFIG_HOME": str(self.config_home)},
            clear=True
        )
        self.environment.start()

    def tearDown(self):
        self.environment.stop()
        self.temporary_directory.cleanup()

    def test_configuration_supplies_signalrot_paths(self):
        source = self.root / "source"
        production = self.root / "production"
        rotbot_config.set_context_binding("signalrot", "source_path", str(source))
        rotbot_config.set_context_binding(
            "signalrot",
            "production_path",
            str(production)
        )

        self.assertEqual(signalrot._repo_path(), source.resolve())
        self.assertEqual(signalrot._web_root(), production.resolve())

    def test_environment_overrides_configuration_per_path(self):
        configured_source = self.root / "configured-source"
        configured_production = self.root / "configured-production"
        environment_source = self.root / "environment-source"
        rotbot_config.set_context_binding(
            "signalrot",
            "source_path",
            str(configured_source)
        )
        rotbot_config.set_context_binding(
            "signalrot",
            "production_path",
            str(configured_production)
        )

        with patch.dict(
            os.environ,
            {"SIGNALROT_REPO": str(environment_source)},
            clear=False
        ):
            self.assertEqual(signalrot._repo_path(), environment_source.resolve())
            self.assertEqual(signalrot._web_root(), configured_production.resolve())

    def test_missing_bindings_return_actionable_messages(self):
        with patch.object(signalrot_paths, "rot_say") as rot_say:
            self.assertIsNone(signalrot._repo_path())
            source_message = rot_say.call_args.args[0]
            rot_say.reset_mock()
            self.assertIsNone(signalrot._web_root())
            production_message = rot_say.call_args.args[0]

        self.assertIn("rot context bind signalrot", source_message)
        self.assertIn("--as source", source_message)
        self.assertIn("--as production", production_message)

    def test_source_command_stops_when_binding_is_missing(self):
        with patch.object(signalrot_paths, "rot_say"), patch.object(
            signalrot,
            "_validate_repo"
        ) as validate:
            result = signalrot.sr_pull(
                argparse.Namespace()
            )

        self.assertEqual(result, 1)
        validate.assert_not_called()

    def test_environment_override_does_not_require_configuration(self):
        malformed = rotbot_config.config_path()
        malformed.parent.mkdir(parents=True)
        malformed.write_text("[broken", encoding="utf-8")
        source = self.root / "source"

        with patch.dict(os.environ, {"SIGNALROT_REPO": str(source)}, clear=False):
            self.assertEqual(signalrot._repo_path(), source.resolve())


if __name__ == "__main__":
    unittest.main()
