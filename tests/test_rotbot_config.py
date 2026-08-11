import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import rotbot_config


class RotbotConfigTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.config = self.root / "config" / "rotbot" / "config.toml"

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_config_path_respects_xdg_config_home(self):
        self.assertEqual(
            rotbot_config.config_path({"XDG_CONFIG_HOME": str(self.root)}),
            self.root / "rotbot" / "config.toml"
        )
        with self.assertRaises(rotbot_config.ConfigError):
            rotbot_config.config_path({"XDG_CONFIG_HOME": "relative"})

    def test_write_preserves_unrelated_toml_and_other_bindings(self):
        self.config.parent.mkdir(parents=True)
        original = (
            'theme = "dead-signal"\n\n'
            "[unrelated]\n"
            "enabled = true\n\n"
            "[contexts.rotbot]\n"
            f'source_path = "{self.root}/rotbot"\n'
        )
        self.config.write_text(original, encoding="utf-8")

        rotbot_config.set_context_binding(
            "signalrot",
            "source_path",
            str(self.root / "signalrot"),
            self.config
        )

        updated = self.config.read_text(encoding="utf-8")
        self.assertIn(original, updated)
        self.assertEqual(
            rotbot_config.get_context_binding("rotbot", self.config)["source_path"],
            str(self.root / "rotbot")
        )
        self.assertEqual(
            rotbot_config.get_context_binding("signalrot", self.config)["source_path"],
            str(self.root / "signalrot")
        )

    def test_update_changes_only_approved_binding(self):
        self.config.parent.mkdir(parents=True)
        self.config.write_text(
            "[contexts.signalrot]\n"
            'source_path = "/old/source"\n'
            'production_path = "/keep/production"\n\n'
            "[contexts.rotbot]\n"
            'source_path = "/keep/rotbot"\n',
            encoding="utf-8"
        )

        rotbot_config.set_context_binding(
            "signalrot",
            "source_path",
            "/new/source",
            self.config
        )

        content = self.config.read_text(encoding="utf-8")
        self.assertIn('source_path = "/new/source"', content)
        self.assertIn('production_path = "/keep/production"', content)
        self.assertIn('source_path = "/keep/rotbot"', content)

    def test_update_supports_valid_header_variants_and_no_final_newline(self):
        self.config.parent.mkdir(parents=True)
        self.config.write_text(
            "[ contexts . 'signalrot' ] # user formatting\n"
            'production_path = "/keep/production"',
            encoding="utf-8"
        )

        rotbot_config.set_context_binding(
            "signalrot",
            "source_path",
            "/new/source",
            self.config
        )

        binding = rotbot_config.get_context_binding("signalrot", self.config)
        self.assertEqual(binding["source_path"], "/new/source")
        self.assertEqual(binding["production_path"], "/keep/production")

    def test_malformed_configuration_is_never_overwritten(self):
        self.config.parent.mkdir(parents=True)
        malformed = "[contexts.signalrot\nsource_path = nope"
        self.config.write_text(malformed, encoding="utf-8")

        with self.assertRaises(rotbot_config.ConfigError):
            rotbot_config.set_context_binding(
                "signalrot",
                "source_path",
                "/new/source",
                self.config
            )

        self.assertEqual(self.config.read_text(encoding="utf-8"), malformed)

    def test_writer_uses_same_directory_atomic_replace(self):
        real_replace = os.replace
        calls = []

        def recording_replace(source, destination):
            calls.append((Path(source), Path(destination)))
            real_replace(source, destination)

        with patch.object(rotbot_config.os, "replace", side_effect=recording_replace):
            rotbot_config.set_context_binding(
                "signalrot",
                "source_path",
                "/source",
                self.config
            )

        self.assertEqual(len(calls), 1)
        source, destination = calls[0]
        self.assertEqual(source.parent, self.config.parent)
        self.assertEqual(destination, self.config)
        self.assertFalse(source.exists())

    def test_failed_atomic_replace_preserves_original_and_cleans_temp_file(self):
        self.config.parent.mkdir(parents=True)
        original = '[contexts.signalrot]\nsource_path = "/old"\n'
        self.config.write_text(original, encoding="utf-8")

        with patch.object(
            rotbot_config.os,
            "replace",
            side_effect=OSError("replace failed")
        ), self.assertRaises(rotbot_config.ConfigError):
            rotbot_config.set_context_binding(
                "signalrot",
                "source_path",
                "/new",
                self.config
            )

        self.assertEqual(self.config.read_text(encoding="utf-8"), original)
        self.assertEqual(tuple(self.config.parent.glob("config.*.tmp")), ())

    def test_symlink_configuration_is_rejected(self):
        target = self.root / "target.toml"
        target.write_text("", encoding="utf-8")
        self.config.parent.mkdir(parents=True)
        self.config.symlink_to(target)

        with self.assertRaises(rotbot_config.ConfigError):
            rotbot_config.set_context_binding(
                "signalrot",
                "source_path",
                "/source",
                self.config
            )


if __name__ == "__main__":
    unittest.main()
