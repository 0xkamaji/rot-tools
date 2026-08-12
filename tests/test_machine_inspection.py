import argparse
import json
import subprocess
import unittest
from unittest.mock import Mock, patch

from rotbot.commands import machine as inspection
from rotbot.contexts import machines


class MachineInspectionTests(unittest.TestCase):
    def test_command_runner_is_bounded_and_never_uses_a_shell(self):
        completed = Mock(returncode=0, stdout="value\n")
        with patch.object(inspection.subprocess, "run", return_value=completed) as run:
            result = inspection._run_command(["safe-command", "argument"])

        self.assertEqual(result, "value")
        run.assert_called_once_with(
            ["safe-command", "argument"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            shell=False,
            timeout=inspection.COMMAND_TIMEOUT,
            env={"PATH": inspection.os.defpath, "LANG": "C", "LC_ALL": "C"}
        )

    def test_missing_failed_timed_out_and_oversized_commands_are_unavailable(self):
        cases = (
            FileNotFoundError(),
            PermissionError(),
            subprocess.TimeoutExpired(["command"], 1),
            Mock(returncode=1, stdout="failed"),
            Mock(returncode=0, stdout="x" * (inspection.MAX_COMMAND_OUTPUT + 1))
        )
        for result in cases:
            with self.subTest(result=result), patch.object(
                inspection.subprocess,
                "run",
                side_effect=result if isinstance(result, BaseException) else None,
                return_value=None if isinstance(result, BaseException) else result
            ):
                self.assertIsNone(inspection._run_command(["command"]))

    def test_architecture_aliases_are_normalized(self):
        expected = {
            "AMD64": "x86_64",
            "x86_64": "x86_64",
            "aarch64": "aarch64",
            "arm64": "aarch64",
            "i686": "x86"
        }
        for value, normalized in expected.items():
            with self.subTest(value=value):
                self.assertEqual(inspection._normalize_architecture(value), normalized)

    def test_linux_inspection_normalizes_and_separates_facts(self):
        cpuinfo = (
            "processor : 0\nmodel name : Example CPU\nphysical id : 0\ncore id : 0\n\n"
            "processor : 1\nmodel name : Example CPU\nphysical id : 0\ncore id : 1\n"
        )

        def read_text(path, limit=1_000_000):
            return {
                "/proc/cpuinfo": cpuinfo,
                "/proc/meminfo": "MemTotal:       33554432 kB\n",
                "/sys/class/dmi/id/chassis_type": "3\n",
                "/proc/device-tree/model": None
            }.get(path)

        def run_command(arguments, timeout=inspection.COMMAND_TIMEOUT):
            if arguments[0] == "nvidia-smi":
                return "Example GPU, 8192"
            if arguments[:3] == ["ip", "-j", "address"]:
                return json.dumps([
                    {
                        "ifname": "eth0",
                        "addr_info": [
                            {"family": "inet", "local": "192.0.2.10"},
                            {"family": "inet", "local": "127.0.0.1"}
                        ]
                    }
                ])
            return None

        with patch.object(
            inspection.platform,
            "machine",
            return_value="AMD64"
        ), patch.object(
            inspection.platform,
            "freedesktop_os_release",
            return_value={"NAME": "CachyOS", "VERSION_ID": "2026.08"}
        ), patch.object(inspection, "_read_text", side_effect=read_text), patch.object(
            inspection,
            "_run_command",
            side_effect=run_command
        ), patch.object(inspection.os, "cpu_count", return_value=4), patch.object(
            inspection.socket,
            "gethostname",
            return_value="desktop-host"
        ), patch.object(inspection.getpass, "getuser", return_value="local-login"), patch.object(
            inspection,
            "_find_command",
            side_effect=lambda name: "/usr/bin/ssh" if name == "ssh" else None
        ):
            facts = inspection.inspect_local_machine(system="Linux")

        self.assertEqual(facts.portable, {
            "device_type": "desktop",
            "operating_system": "CachyOS",
            "operating_system_version": "2026.08",
            "architecture": "x86_64",
            "cpu": {
                "model": "Example CPU",
                "physical_cores": 2,
                "logical_cores": 4
            },
            "memory": {"total_gb": 32},
            "gpus": [{"model": "Example GPU", "vram_gb": 8}]
        })
        self.assertNotIn("hostname", facts.portable)
        self.assertEqual(facts.local["connection"], {
            "hostname": "desktop-host",
            "ssh_available": True
        })
        self.assertEqual(
            facts.local["network"],
            [{"interface": "eth0", "address": "192.0.2.10"}]
        )
        self.assertEqual(
            facts.local["users"],
            [{"username": "local-login", "role": "current-user"}]
        )

    def test_macos_collector_uses_fixed_sysctl_commands(self):
        values = {
            "machdep.cpu.brand_string": "Apple M4",
            "hw.physicalcpu": "10",
            "hw.logicalcpu": "10",
            "hw.memsize": str(16 * 1024 ** 3),
            "hw.model": "MacBookPro21,1"
        }

        def run_command(arguments, timeout=inspection.COMMAND_TIMEOUT):
            if arguments[0] == "/usr/sbin/system_profiler":
                self.assertEqual(timeout, 5)
                return json.dumps({
                    "SPDisplaysDataType": [
                        {"sppci_model": "Apple M4", "spdisplays_vram_shared": "16 GB"}
                    ]
                })
            self.assertEqual(arguments[:2], ["/usr/sbin/sysctl", "-n"])
            return values[arguments[2]]

        portable = {}
        with patch.object(
            inspection.platform,
            "mac_ver",
            return_value=("15.0", (), "")
        ), patch.object(inspection, "_run_command", side_effect=run_command):
            inspection._collect_macos(portable, {})

        self.assertEqual(portable["operating_system"], "macOS")
        self.assertEqual(portable["device_type"], "laptop")
        self.assertEqual(portable["cpu"]["model"], "Apple M4")
        self.assertEqual(portable["memory"], {"total_gb": 16})
        self.assertEqual(portable["gpus"], [{"model": "Apple M4", "vram_gb": 16}])

    def test_windows_collector_uses_fixed_noninteractive_powershell(self):
        payload = json.dumps({
            "cpu": {
                "Name": "Example CPU",
                "NumberOfCores": 8,
                "NumberOfLogicalProcessors": 16
            },
            "memory": 32 * 1024 ** 3,
            "gpus": [{"Name": "Example GPU", "AdapterRAM": 8 * 1024 ** 3}]
        })
        portable = {}
        with patch.dict(
            inspection.os.environ,
            {"SystemRoot": "C:\\Windows"}
        ), patch.object(
            inspection.platform,
            "win32_ver",
            return_value=("11", "2026", "", "")
        ), patch.object(inspection, "_run_command", return_value=payload) as run:
            inspection._collect_windows(portable, {})

        arguments = run.call_args.args[0]
        self.assertEqual(
            arguments[:3],
            [
                "C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe",
                "-NoProfile",
                "-NonInteractive"
            ]
        )
        self.assertEqual(run.call_args.kwargs["timeout"], 5)
        self.assertEqual(portable["memory"], {"total_gb": 32})
        self.assertEqual(portable["gpus"][0]["vram_gb"], 8)

    def test_tailscale_detection_uses_status_only(self):
        local = {}
        payload = json.dumps({
            "Self": {
                "DNSName": "desktop.tailnet.ts.net.",
                "TailscaleIPs": ["100.64.0.1"]
            }
        })
        with patch.object(inspection, "_find_command", return_value="/bin/tailscale"), patch.object(
            inspection,
            "_run_command",
            return_value=payload
        ) as run:
            inspection._add_tailscale(local)

        run.assert_called_once_with(["/bin/tailscale", "status", "--json"])
        self.assertEqual(
            local["connection"]["tailscale_name"],
            "desktop.tailnet.ts.net"
        )
        self.assertEqual(
            local["network"],
            [{"interface": "tailscale", "address": "100.64.0.1"}]
        )

    def test_configured_machine_inspect_prints_without_overwriting_context(self):
        facts = inspection.MachineInspection(
            {"operating_system": "CachyOS", "architecture": "x86_64"},
            {"connection": {"hostname": "desktop-host"}}
        )
        with patch.object(
            inspection,
            "get_local_context_bindings",
            return_value={"machine": "00000000-0000-4000-8000-000000000003"}
        ), patch.object(
            machines,
            "load_machine_context_reference",
            return_value=machines.MachineContext(
                "desktop",
                "Desktop",
                {},
                "00000000-0000-4000-8000-000000000003"
            )
        ), patch.object(
            machines,
            "associated_machine_context",
            return_value=None
        ), patch.object(
            inspection,
            "inspect_local_machine",
            return_value=facts
        ), patch.object(inspection, "rot_say") as rot_say, patch.object(
            inspection,
            "rot_continue"
        ) as rot_continue, patch.object(
            machines,
            "create_machine"
        ) as create_machine, patch.object(
            machines,
            "create_local_machine_record"
        ) as create_local:
            result = inspection.machine_inspect(argparse.Namespace())

        self.assertEqual(result, 0)
        messages = [call.args[0] for call in rot_say.call_args_list]
        self.assertEqual(messages[:2], [
            "Portable machine metadata", "Local machine metadata"
        ])
        self.assertIn("already exists", messages[2])
        output = "\n".join(call.args[0] for call in rot_continue.call_args_list)
        self.assertIn("Operating system: CachyOS", output)
        self.assertIn("Hostname: desktop-host", output)
        create_machine.assert_not_called()
        create_local.assert_not_called()

    def test_configured_duplicate_can_rebind_to_verified_existing_machine(self):
        facts = inspection.MachineInspection(
            {}, {"connection": {"hostname": "desktop-host"}}
        )
        configured = machines.MachineContext(
            "desktop-host",
            "Desktop Host",
            {},
            "00000000-0000-4000-8000-000000000003"
        )
        matched = machines.MachineContext(
            "existing-laptop",
            "Existing Laptop",
            {},
            "00000000-0000-4000-8000-000000000004"
        )
        with patch.object(
            inspection,
            "inspect_local_machine",
            return_value=facts
        ), patch.object(inspection, "show_inspection"), patch.object(
            inspection,
            "get_local_context_bindings",
            return_value={"machine": configured.id}
        ), patch.object(
            machines,
            "load_machine_context_reference",
            return_value=configured
        ), patch.object(
            machines,
            "associated_machine_context",
            return_value=matched
        ), patch.object(
            inspection,
            "_confirm_registration",
            return_value=True
        ), patch.object(
            inspection,
            "set_local_context_binding"
        ) as set_binding, patch.object(inspection, "rot_say"):
            result = inspection.machine_inspect(argparse.Namespace())

        self.assertEqual(result, 0)
        set_binding.assert_called_once_with("machine", matched.id)

    def test_unconfigured_machine_inspect_uses_shared_registration(self):
        facts = inspection.MachineInspection(
            {"operating_system": "TestOS"},
            {"connection": {"hostname": "desktop-host"}}
        )
        with patch.object(
            inspection,
            "inspect_local_machine",
            return_value=facts
        ), patch.object(inspection, "show_inspection"), patch.object(
            inspection,
            "get_local_context_bindings",
            return_value={}
        ), patch.object(
            machines,
            "associated_machine_context",
            return_value=None
        ), patch.object(
            inspection,
            "_ask_machine_display_name",
            return_value="Studio Workstation"
        ), patch.object(
            inspection,
            "_confirm_registration",
            return_value=True
        ), patch.object(
            inspection,
            "register_local_machine"
        ) as register, patch.object(inspection, "rot_say"):
            result = inspection.machine_inspect(argparse.Namespace())

        self.assertEqual(result, 0)
        register.assert_called_once_with(
            facts,
            display=False,
            existing_machine=None,
            display_name="Studio Workstation"
        )

    def test_registration_failure_is_reported(self):
        facts = inspection.MachineInspection(
            {}, {"connection": {"hostname": "desktop-host"}}
        )
        with patch.object(
            inspection,
            "inspect_local_machine",
            return_value=facts
        ), patch.object(inspection, "show_inspection"), patch.object(
            inspection,
            "get_local_context_bindings",
            return_value={}
        ), patch.object(
            machines,
            "associated_machine_context",
            return_value=None
        ), patch.object(
            inspection,
            "_ask_machine_display_name",
            return_value="Desktop Host"
        ), patch.object(
            inspection,
            "_confirm_registration",
            return_value=True
        ), patch.object(
            inspection,
            "register_local_machine",
            side_effect=inspection.MachineRegistrationError("inspection failed")
        ), patch.object(inspection, "rot_say") as rot_say:
            result = inspection.machine_inspect(argparse.Namespace())

        self.assertEqual(result, 1)
        self.assertIn("inspection failed", rot_say.call_args.args[0])

    def test_declined_registration_only_displays_inspection(self):
        facts = inspection.MachineInspection(
            {}, {"connection": {"hostname": "desktop-host"}}
        )
        with patch.object(
            inspection,
            "inspect_local_machine",
            return_value=facts
        ), patch.object(inspection, "show_inspection") as show, patch.object(
            inspection,
            "get_local_context_bindings",
            return_value={}
        ), patch.object(
            machines,
            "associated_machine_context",
            return_value=None
        ), patch.object(
            inspection,
            "_ask_machine_display_name",
            return_value="Desktop Host"
        ), patch.object(
            inspection,
            "_confirm_registration",
            return_value=False
        ), patch.object(inspection, "register_local_machine") as register, patch.object(
            inspection,
            "rot_say"
        ):
            result = inspection.machine_inspect(argparse.Namespace())

        self.assertEqual(result, 0)
        show.assert_called_once_with(facts)
        register.assert_not_called()

    def test_display_name_eof_cancels_before_confirmation(self):
        facts = inspection.MachineInspection(
            {}, {"connection": {"hostname": "desktop-host"}}
        )
        with patch.object(
            inspection,
            "inspect_local_machine",
            return_value=facts
        ), patch.object(inspection, "show_inspection"), patch.object(
            inspection,
            "get_local_context_bindings",
            return_value={}
        ), patch.object(
            machines,
            "associated_machine_context",
            return_value=None
        ), patch.object(
            inspection,
            "_ask_machine_display_name",
            return_value=None
        ), patch.object(inspection, "_confirm_registration") as confirm, patch.object(
            inspection,
            "register_local_machine"
        ) as register, patch.object(inspection, "rot_say"):
            result = inspection.machine_inspect(argparse.Namespace())

        self.assertEqual(result, 0)
        confirm.assert_not_called()
        register.assert_not_called()

    def test_existing_machine_does_not_prompt_for_display_name(self):
        facts = inspection.MachineInspection(
            {}, {"connection": {"hostname": "desktop-host"}}
        )
        machine = machines.MachineContext(
            "desktop",
            "Desktop",
            {},
            "00000000-0000-4000-8000-000000000003"
        )
        with patch.object(
            inspection,
            "inspect_local_machine",
            return_value=facts
        ), patch.object(inspection, "show_inspection"), patch.object(
            inspection,
            "get_local_context_bindings",
            return_value={"machine": machine.id}
        ), patch.object(
            machines,
            "load_machine_context_reference",
            return_value=machine
        ), patch.object(
            machines,
            "associated_machine_context",
            return_value=machine
        ), patch.object(
            inspection,
            "_ask_machine_display_name"
        ) as ask_display, patch.object(inspection, "rot_say"):
            result = inspection.machine_inspect(argparse.Namespace())

        self.assertEqual(result, 0)
        ask_display.assert_not_called()

    def test_legacy_name_binding_is_not_migrated_without_confirmation(self):
        facts = inspection.MachineInspection(
            {}, {"connection": {"hostname": "desktop-host"}}
        )
        machine = machines.MachineContext(
            "desktop",
            "Desktop",
            {},
            "00000000-0000-4000-8000-000000000003"
        )
        with patch.object(
            inspection,
            "inspect_local_machine",
            return_value=facts
        ), patch.object(inspection, "show_inspection"), patch.object(
            inspection,
            "get_local_context_bindings",
            return_value={"machine": "desktop"}
        ), patch.object(
            machines,
            "load_machine_context_reference",
            return_value=machine
        ), patch.object(
            machines,
            "associated_machine_context",
            return_value=None
        ), patch.object(
            inspection,
            "set_local_context_binding"
        ) as set_binding, patch.object(inspection, "rot_say"):
            result = inspection.machine_inspect(argparse.Namespace())

        self.assertEqual(result, 0)
        set_binding.assert_not_called()

    def test_empty_sections_are_reported_cleanly(self):
        with patch.object(inspection, "rot_say"), patch.object(
            inspection,
            "rot_continue"
        ) as rot_continue:
            inspection.show_inspection(inspection.MachineInspection({}, {}))

        self.assertEqual(
            [call.args[0] for call in rot_continue.call_args_list],
            ["No portable facts detected.", "No local facts detected."]
        )


if __name__ == "__main__":
    unittest.main()
