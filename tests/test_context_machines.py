import os
from pathlib import Path
import stat
import tempfile
import tomllib
import unittest
from unittest.mock import patch

from rotbot.contexts import machines


class MachineContextTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.machines_root = self.root / "context" / "machines"
        self.machines_root.mkdir(parents=True)
        self.config = self.root / "config" / "rotbot" / "config.toml"

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_creation_writes_exact_three_portable_files(self):
        destination = machines.create_machine(
            "desktop", "Main Desktop", machines_root=self.machines_root
        )

        self.assertEqual(
            {path.name for path in destination.iterdir()},
            {"metadata.toml", "identity.md", "software.toml"}
        )

    def test_metadata_serializes_normalized_portable_facts(self):
        facts = {
            "device_type": "desktop",
            "operating_system": "CachyOS",
            "operating_system_version": "2026.08",
            "architecture": "x86_64",
            "cpu": {
                "model": "AMD Ryzen 7 5800X",
                "physical_cores": 8,
                "logical_cores": 16
            },
            "memory": {"total_gb": 32},
            "gpus": [
                {"model": "NVIDIA GeForce GTX 1070", "vram_gb": 8}
            ]
        }
        destination = machines.create_machine(
            "desktop",
            "Main Desktop",
            facts,
            machines_root=self.machines_root
        )

        metadata = tomllib.loads(
            (destination / "metadata.toml").read_text(encoding="utf-8")
        )
        self.assertEqual(metadata, {
            "type": "machine",
            "name": "desktop",
            "display_name": "Main Desktop",
            **facts
        })

    def test_minimal_metadata_omits_unknown_values(self):
        destination = machines.create_machine(
            "desktop", "Desktop", machines_root=self.machines_root
        )

        metadata = tomllib.loads(
            (destination / "metadata.toml").read_text(encoding="utf-8")
        )
        self.assertEqual(metadata, {
            "type": "machine",
            "name": "desktop",
            "display_name": "Desktop"
        })
        self.assertNotIn("unknown", (destination / "metadata.toml").read_text())

    def test_identity_and_software_templates_are_natural_and_valid(self):
        files = machines.render_machine_files(
            machines.build_machine_context("desktop", "Desktop")
        )

        identity = files["identity.md"]
        for heading in (
            "# Identity",
            "## Purpose",
            "## Environment",
            "## Important Context"
        ):
            self.assertIn(heading, identity)
        self.assertIn("<!-- A general overview of this machine", identity)
        self.assertIn("<!-- Useful information about the machine", identity)
        self.assertEqual(tomllib.loads(files["software.toml"]), {})

    def test_loader_accepts_freeform_identity_and_ordered_software(self):
        destination = machines.create_machine(
            "desktop", machines_root=self.machines_root
        )
        (destination / "identity.md").write_text(
            "Manually edited without template headings.\n",
            encoding="utf-8"
        )
        (destination / "software.toml").write_text(
            '[[software]]\nname = "Ollama"\ncategory = "local-ai"\n\n'
            '[[software]]\nname = "OpenCode"\ncategory = "development"\n',
            encoding="utf-8"
        )

        machine, documents = machines.load_machine_files(
            "desktop", machines_root=self.machines_root
        )

        self.assertEqual(machine.name, "desktop")
        content = {document.filename: document.content for document in documents}
        software = tomllib.loads(content["software.toml"])["software"]
        self.assertEqual([entry["name"] for entry in software], ["Ollama", "OpenCode"])
        self.assertIn("without template headings", content["identity.md"])

    def test_listing_discovers_only_valid_machine_contexts(self):
        machines.create_machine("zeta", machines_root=self.machines_root)
        machines.create_machine("alpha", machines_root=self.machines_root)
        invalid = self.machines_root / "invalid"
        invalid.mkdir()
        (invalid / "metadata.toml").write_text(
            'type = "person"\nname = "invalid"\n', encoding="utf-8"
        )

        self.assertEqual(
            tuple(
                machine.name
                for machine in machines.list_machine_contexts(
                    machines_root=self.machines_root
                )
            ),
            ("alpha", "zeta")
        )

    def test_invalid_names_and_facts_write_nothing(self):
        calls = (
            (("",), None),
            (("../desktop",), None),
            (("nested/desktop",), None),
            (("/tmp/desktop",), None),
            (("desktop", "Desktop", {"hostname": "private"}), None),
            (("desktop", "Desktop", {"cpu": {"physical_cores": 0}}), None),
            (("desktop", "Desktop", {"gpus": [{"vram_gb": 8}]}), None),
            (("desktop", "bad\nname"), None)
        )
        for args, _unused in calls:
            with self.subTest(args=args), self.assertRaises(
                machines.MachineContextError
            ):
                machines.create_machine(*args, machines_root=self.machines_root)
        self.assertEqual(tuple(self.machines_root.iterdir()), ())

    def test_existing_machine_is_not_overwritten(self):
        destination = self.machines_root / "desktop"
        destination.mkdir()
        marker = destination / "marker.txt"
        marker.write_text("unchanged", encoding="utf-8")

        with self.assertRaises(machines.MachineContextError):
            machines.create_machine("desktop", machines_root=self.machines_root)

        self.assertEqual(marker.read_text(encoding="utf-8"), "unchanged")

    def test_failed_portable_creation_removes_only_partial_machine(self):
        existing = self.machines_root / "existing"
        existing.mkdir()
        marker = existing / "marker.txt"
        marker.write_text("keep", encoding="utf-8")
        original_write = machines._write_document
        writes = 0

        def fail_second_write(path, content, mode=None):
            nonlocal writes
            writes += 1
            if writes == 2:
                raise OSError("write failed")
            original_write(path, content, mode)

        with patch.object(machines, "_write_document", side_effect=fail_second_write):
            with self.assertRaises(machines.MachineContextError):
                machines.create_machine("desktop", machines_root=self.machines_root)

        self.assertFalse((self.machines_root / "desktop").exists())
        self.assertEqual(marker.read_text(encoding="utf-8"), "keep")

    def test_local_path_uses_config_directory_and_same_machine_name(self):
        self.assertEqual(
            machines.local_machine_record_path(
                "desktop", target_config=self.config
            ),
            self.config.parent / "machines" / "desktop.toml"
        )
        xdg_home = self.root / "xdg"
        with patch.dict(os.environ, {"XDG_CONFIG_HOME": str(xdg_home)}, clear=True):
            self.assertEqual(
                machines.local_machine_record_path("desktop"),
                xdg_home / "rot" / "machines" / "desktop.toml"
            )
        with self.assertRaises(machines.MachineContextError):
            machines.local_machines_directory(target_config=Path("config.toml"))

    def test_local_record_is_one_private_toml_with_approved_facts(self):
        facts = {
            "connection": {
                "hostname": "desktop-host",
                "tailscale_name": "desktop-tailnet",
                "ssh_available": True
            },
            "network": [{"interface": "tailscale0", "address": "100.1.2.3"}],
            "services": [
                {"name": "ssh", "port": 22, "protocol": "tcp", "access": "tailscale"}
            ],
            "users": [{"username": "local-login", "role": "current-user"}]
        }
        destination = machines.create_local_machine_record(
            "desktop", facts, target_config=self.config
        )

        self.assertEqual(destination, self.config.parent / "machines" / "desktop.toml")
        self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o600)
        document = tomllib.loads(destination.read_text(encoding="utf-8"))
        self.assertEqual(document["machine_ref"], "desktop")
        self.assertEqual(document["connection"], facts["connection"])
        self.assertEqual(document["network"], facts["network"])
        self.assertEqual(document["services"], facts["services"])
        self.assertEqual(document["users"], facts["users"])

    def test_empty_or_existing_local_record_is_rejected(self):
        with self.assertRaises(machines.MachineContextError):
            machines.create_local_machine_record(
                "desktop", {}, target_config=self.config
            )
        destination = machines.create_local_machine_record(
            "desktop",
            {"connection": {"hostname": "desktop-host"}},
            target_config=self.config
        )

        with self.assertRaises(machines.MachineContextError):
            machines.create_local_machine_record(
                "desktop",
                {"connection": {"hostname": "replacement"}},
                target_config=self.config
            )

        self.assertIn("desktop-host", destination.read_text(encoding="utf-8"))

    def test_missing_local_record_is_not_configured(self):
        self.assertIsNone(
            machines.load_local_machine_record(
                "desktop", target_config=self.config
            )
        )

    def test_local_loader_reads_legacy_record_when_canonical_is_missing(self):
        xdg_home = self.root / "xdg"
        legacy = xdg_home / "rotbot" / "machines" / "desktop.toml"
        legacy.parent.mkdir(parents=True)
        legacy.write_text(
            'machine_ref = "desktop"\n'
            "[connection]\n"
            'hostname = "legacy-host"\n',
            encoding="utf-8"
        )
        legacy.chmod(0o600)

        with patch.dict(os.environ, {"XDG_CONFIG_HOME": str(xdg_home)}, clear=True):
            loaded = machines.load_local_machine_record("desktop")

        self.assertEqual(loaded["connection"]["hostname"], "legacy-host")

    def test_local_loader_rejects_secret_fields(self):
        destination = machines.local_machine_record_path(
            "desktop", target_config=self.config
        )
        destination.parent.mkdir(parents=True)
        destination.write_text(
            'machine_ref = "desktop"\n[connection]\npassword = "never"\n',
            encoding="utf-8"
        )
        destination.chmod(0o600)

        with self.assertRaisesRegex(machines.MachineContextError, "not allowed"):
            machines.load_local_machine_record(
                "desktop", target_config=self.config
            )

        with self.assertRaises(machines.MachineContextError):
            machines.render_local_machine_record(
                "desktop",
                {"connection": {"authorization": "Bearer never-store-this"}}
            )


if __name__ == "__main__":
    unittest.main()
