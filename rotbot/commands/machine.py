import getpass
import ipaddress
import json
import os
from pathlib import Path, PureWindowsPath
import platform
import re
import shutil
import socket
import subprocess
from typing import NamedTuple

from rotbot.contexts import loader, machines
from rotbot.contexts.config import (
    ConfigError,
    get_local_context_bindings,
    set_local_context_binding
)
from rotbot.contexts.machines import validate_local_facts, validate_portable_facts
from rotbot.ui.terminal import rot_continue, rot_say


COMMAND_TIMEOUT = 3
MAX_COMMAND_OUTPUT = 1_000_000


class MachineInspection(NamedTuple):
    portable: dict
    local: dict


class MachineRegistrationError(Exception):
    pass


def _detected_text(value, limit=1000):
    if not isinstance(value, str):
        return None
    value = value.strip()
    if (
        not value
        or len(value) > limit
        or any(ord(character) < 32 for character in value)
    ):
        return None
    return value


def _find_command(name):
    if os.name != "nt":
        return shutil.which(name, path=os.defpath)
    directories = []
    system_root = os.environ.get("SystemRoot") or os.environ.get("WINDIR")
    if system_root:
        directories.extend((
            str(PureWindowsPath(system_root) / "System32"),
            str(PureWindowsPath(system_root) / "System32" / "OpenSSH")
        ))
    for variable in ("ProgramFiles", "ProgramFiles(x86)"):
        base = os.environ.get(variable)
        if base:
            directories.append(str(PureWindowsPath(base) / "Tailscale"))
    command = shutil.which(name, path=os.pathsep.join(directories)) if directories else None
    return command if command and PureWindowsPath(command).is_absolute() else None


def _run_command(arguments, timeout=COMMAND_TIMEOUT):
    environment = {"PATH": os.defpath, "LANG": "C", "LC_ALL": "C"}
    if os.name == "nt":
        system_root = os.environ.get("SystemRoot") or os.environ.get("WINDIR")
        if system_root:
            environment.update({"SystemRoot": system_root, "WINDIR": system_root})
    try:
        result = subprocess.run(
            arguments,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            shell=False,
            timeout=timeout,
            env=environment
        )
    except (FileNotFoundError, PermissionError, subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0 or len(result.stdout.encode("utf-8")) > MAX_COMMAND_OUTPUT:
        return None
    return result.stdout.strip()


def _read_text(path, limit=1_000_000):
    try:
        content = Path(path).read_bytes()
    except OSError:
        return None
    if len(content) > limit or b"\0" in content:
        return None
    try:
        return content.decode("utf-8")
    except UnicodeError:
        return None


def _normalize_architecture(value):
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    aliases = {
        "amd64": "x86_64",
        "x64": "x86_64",
        "i386": "x86",
        "i486": "x86",
        "i586": "x86",
        "i686": "x86",
        "arm64": "aarch64"
    }
    normalized = aliases.get(normalized, normalized)
    if not normalized or any(
        not (character.isalnum() or character in "._-")
        for character in normalized
    ):
        return None
    return normalized


def _gb_from_bytes(value):
    if not isinstance(value, int) or value <= 0:
        return None
    gigabytes = round(value / (1024 ** 3), 2)
    return int(gigabytes) if gigabytes.is_integer() else gigabytes


def _linux_cpu():
    content = _read_text("/proc/cpuinfo")
    if not content:
        return {}
    model = None
    physical_cores = set()
    processor = {}
    for line in content.splitlines() + [""]:
        if not line.strip():
            if model is None:
                model = _detected_text(
                    processor.get("model name") or processor.get("hardware")
                )
            physical_id = processor.get("physical id")
            core_id = processor.get("core id")
            if physical_id is not None and core_id is not None:
                physical_cores.add((physical_id, core_id))
            processor = {}
            continue
        if ":" in line:
            key, value = line.split(":", 1)
            processor[key.strip().lower()] = value.strip()
    cpu = {}
    if model:
        cpu["model"] = model
    if physical_cores:
        cpu["physical_cores"] = len(physical_cores)
    logical = os.cpu_count()
    if logical:
        cpu["logical_cores"] = logical
    return cpu


def _linux_memory():
    content = _read_text("/proc/meminfo", limit=100_000)
    if not content:
        return None
    for line in content.splitlines():
        if line.startswith("MemTotal:"):
            parts = line.split()
            if len(parts) >= 2 and parts[1].isdigit():
                return _gb_from_bytes(int(parts[1]) * 1024)
    return None


def _linux_device_type():
    chassis = _read_text("/sys/class/dmi/id/chassis_type", limit=100)
    if chassis and chassis.strip().isdigit():
        value = int(chassis.strip())
        if value in {8, 9, 10, 11, 12, 14}:
            return "laptop"
        if value in {3, 4, 5, 6, 7, 13, 15, 16}:
            return "desktop"
        if value in {17, 23, 28, 29}:
            return "server"
    model = _read_text("/proc/device-tree/model", limit=10_000)
    if model and any(name in model.lower() for name in ("raspberry pi", "beaglebone")):
        return "single-board-computer"
    return None


def _linux_gpus():
    output = _run_command(
        [
            "nvidia-smi",
            "--query-gpu=name,memory.total",
            "--format=csv,noheader,nounits"
        ]
    )
    gpus = []
    if output:
        for line in output.splitlines():
            name, separator, memory = line.rpartition(",")
            model = _detected_text(name)
            if separator and model:
                gpu = {"model": model}
                if memory.strip().isdigit():
                    vram = _gb_from_bytes(int(memory.strip()) * 1024 * 1024)
                    if vram:
                        gpu["vram_gb"] = vram
                gpus.append(gpu)
    if gpus:
        return gpus
    output = _run_command(["lspci", "-mm"])
    if not output:
        return []
    for line in output.splitlines():
        lowered = line.lower()
        if "vga compatible controller" not in lowered and "3d controller" not in lowered:
            continue
        quoted = [part for index, part in enumerate(line.split('"')) if index % 2]
        model = _detected_text(" ".join(quoted[-2:])) if quoted else None
        if model:
            gpus.append({"model": model})
    return gpus


def _linux_network():
    output = _run_command(["ip", "-j", "address", "show"])
    if not output:
        return []
    try:
        interfaces = json.loads(output)
    except (TypeError, json.JSONDecodeError):
        return []
    network = []
    for interface in interfaces if isinstance(interfaces, list) else ():
        if not isinstance(interface, dict):
            continue
        interface_name = _detected_text(interface.get("ifname"), limit=100)
        if interface_name is None:
            continue
        for address in interface.get("addr_info", ()):
            if not isinstance(address, dict) or address.get("family") not in {"inet", "inet6"}:
                continue
            value = address.get("local")
            if _safe_network_address(value):
                network.append({"interface": interface_name, "address": value})
    return network


def _collect_linux(portable, local):
    try:
        release = platform.freedesktop_os_release()
    except (OSError, AttributeError):
        release = {}
    name = _detected_text(release.get("NAME") or platform.system())
    version = _detected_text(release.get("VERSION_ID"))
    if name:
        portable["operating_system"] = name
    if version:
        portable["operating_system_version"] = version
    cpu = _linux_cpu()
    if cpu:
        portable["cpu"] = cpu
    memory = _linux_memory()
    if memory:
        portable["memory"] = {"total_gb": memory}
    device_type = _linux_device_type()
    if device_type:
        portable["device_type"] = device_type
    gpus = _linux_gpus()
    if gpus:
        portable["gpus"] = gpus
    network = _linux_network()
    if network:
        local["network"] = network


def _sysctl(name):
    return _run_command(["/usr/sbin/sysctl", "-n", name])


def _vram_gb(value):
    if isinstance(value, int):
        return _gb_from_bytes(value)
    if not isinstance(value, str):
        return None
    parts = value.strip().split()
    if len(parts) < 2:
        return None
    try:
        amount = float(parts[0])
    except ValueError:
        return None
    unit = parts[1].upper()
    if unit == "GB":
        return int(amount) if amount.is_integer() else amount
    if unit == "MB":
        result = round(amount / 1024, 2)
        return int(result) if result.is_integer() else result
    return None


def _collect_macos(portable, local):
    version = _detected_text(platform.mac_ver()[0])
    portable["operating_system"] = "macOS"
    if version:
        portable["operating_system_version"] = version
    model = _sysctl("machdep.cpu.brand_string")
    physical = _sysctl("hw.physicalcpu")
    logical = _sysctl("hw.logicalcpu")
    cpu = {}
    model = _detected_text(model)
    if model:
        cpu["model"] = model
    if physical and physical.isdigit() and int(physical) > 0:
        cpu["physical_cores"] = int(physical)
    if logical and logical.isdigit() and int(logical) > 0:
        cpu["logical_cores"] = int(logical)
    if cpu:
        portable["cpu"] = cpu
    memory = _sysctl("hw.memsize")
    if memory and memory.isdigit():
        total = _gb_from_bytes(int(memory))
        if total:
            portable["memory"] = {"total_gb": total}
    hardware_model = _sysctl("hw.model") or ""
    if hardware_model.startswith("MacBook"):
        portable["device_type"] = "laptop"
    elif hardware_model.startswith(("iMac", "Macmini", "MacPro", "MacStudio")):
        portable["device_type"] = "desktop"
    output = _run_command(
        ["/usr/sbin/system_profiler", "SPDisplaysDataType", "-json"],
        timeout=5
    )
    if output:
        try:
            displays = json.loads(output).get("SPDisplaysDataType", ())
        except (AttributeError, json.JSONDecodeError):
            displays = ()
        gpus = []
        for display in displays if isinstance(displays, list) else ():
            if not isinstance(display, dict):
                continue
            model = _detected_text(
                display.get("sppci_model") or display.get("_name")
            )
            if not model:
                continue
            gpu = {"model": model}
            vram = _vram_gb(
                display.get("spdisplays_vram")
                or display.get("spdisplays_vram_shared")
            )
            if vram:
                gpu["vram_gb"] = vram
            gpus.append(gpu)
        if gpus:
            portable["gpus"] = gpus


def _collect_windows(portable, local):
    version = _detected_text(platform.win32_ver()[1] or platform.win32_ver()[0])
    portable["operating_system"] = "Windows"
    if version:
        portable["operating_system_version"] = version
    script = (
        "$cpu=Get-CimInstance Win32_Processor|Select-Object -First 1 Name,NumberOfCores,NumberOfLogicalProcessors;"
        "$mem=(Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory;"
        "$gpu=Get-CimInstance Win32_VideoController|Select-Object Name,AdapterRAM;"
        "@{cpu=$cpu;memory=$mem;gpus=$gpu}|ConvertTo-Json -Depth 4 -Compress"
    )
    system_root = os.environ.get("SystemRoot") or os.environ.get("WINDIR")
    root = PureWindowsPath(system_root) if system_root else None
    powershell = (
        str(root / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe")
        if root is not None and root.is_absolute()
        else None
    )
    output = (
        _run_command(
            [powershell, "-NoProfile", "-NonInteractive", "-Command", script],
            timeout=5
        )
        if powershell is not None
        else None
    )
    if not output:
        logical = os.cpu_count()
        if logical:
            portable["cpu"] = {"logical_cores": logical}
        return
    try:
        data = json.loads(output)
    except json.JSONDecodeError:
        return
    cpu_data = data.get("cpu") if isinstance(data, dict) else None
    if isinstance(cpu_data, dict):
        cpu = {}
        model = _detected_text(cpu_data.get("Name"))
        if model:
            cpu["model"] = model
        for source, target in (
            ("NumberOfCores", "physical_cores"),
            ("NumberOfLogicalProcessors", "logical_cores")
        ):
            if isinstance(cpu_data.get(source), int) and cpu_data[source] > 0:
                cpu[target] = cpu_data[source]
        if cpu:
            portable["cpu"] = cpu
    memory = data.get("memory") if isinstance(data, dict) else None
    if isinstance(memory, int):
        total = _gb_from_bytes(memory)
        if total:
            portable["memory"] = {"total_gb": total}
    gpu_data = data.get("gpus", ()) if isinstance(data, dict) else ()
    if isinstance(gpu_data, dict):
        gpu_data = [gpu_data]
    gpus = []
    for item in gpu_data if isinstance(gpu_data, list) else ():
        model = _detected_text(item.get("Name")) if isinstance(item, dict) else None
        if model:
            gpu = {"model": model}
            if isinstance(item.get("AdapterRAM"), int):
                vram = _gb_from_bytes(item["AdapterRAM"])
                if vram:
                    gpu["vram_gb"] = vram
            gpus.append(gpu)
    if gpus:
        portable["gpus"] = gpus


def _safe_network_address(value):
    if not isinstance(value, str):
        return False
    try:
        address = ipaddress.ip_address(value.split("%", 1)[0])
    except ValueError:
        return False
    return not (address.is_loopback or address.is_link_local or address.is_unspecified)


def _fallback_network():
    try:
        addresses = socket.getaddrinfo(socket.gethostname(), None)
    except OSError:
        return []
    values = []
    for address in addresses:
        value = address[4][0]
        if _safe_network_address(value) and value not in values:
            values.append(value)
    return [{"interface": "host", "address": value} for value in values]


def _add_tailscale(local):
    command = _find_command("tailscale")
    if command is None:
        return
    output = _run_command([command, "status", "--json"])
    if not output:
        return
    try:
        data = json.loads(output)
    except json.JSONDecodeError:
        return
    self_data = data.get("Self") if isinstance(data, dict) else None
    if not isinstance(self_data, dict):
        return
    dns_name = self_data.get("DNSName")
    dns_name = _detected_text(dns_name)
    if dns_name and dns_name.strip("."):
        local.setdefault("connection", {})["tailscale_name"] = dns_name.strip(".")
    existing = {(item["interface"], item["address"]) for item in local.get("network", ())}
    for address in self_data.get("TailscaleIPs", ()):
        item = ("tailscale", address)
        if _safe_network_address(address) and item not in existing:
            local.setdefault("network", []).append(
                {"interface": "tailscale", "address": address}
            )


def inspect_local_machine(system=None):
    portable = {}
    local = {}
    architecture = _normalize_architecture(platform.machine())
    if architecture:
        portable["architecture"] = architecture
    system = platform.system() if system is None else system
    if system == "Linux":
        _collect_linux(portable, local)
    elif system == "Darwin":
        _collect_macos(portable, local)
    elif system == "Windows":
        _collect_windows(portable, local)
    elif _detected_text(system):
        portable["operating_system"] = _detected_text(system)
        logical = os.cpu_count()
        if logical:
            portable["cpu"] = {"logical_cores": logical}
    hostname = _detected_text(socket.gethostname())
    if hostname:
        local.setdefault("connection", {})["hostname"] = hostname
    try:
        username = _detected_text(getpass.getuser()) or ""
    except (KeyError, OSError):
        username = ""
    if username:
        local["users"] = [{"username": username, "role": "current-user"}]
    if not local.get("network"):
        network = _fallback_network()
        if network:
            local["network"] = network
    if _find_command("ssh") is not None:
        local.setdefault("connection", {})["ssh_available"] = True
    _add_tailscale(local)
    return MachineInspection(
        validate_portable_facts(portable),
        validate_local_facts(local)
    )


def _portable_lines(portable):
    labels = (
        ("operating_system", "Operating system"),
        ("operating_system_version", "OS version"),
        ("architecture", "Architecture"),
        ("device_type", "Device type")
    )
    lines = [f"{label}: {portable[key]}" for key, label in labels if key in portable]
    cpu = portable.get("cpu", {})
    for key, label in (
        ("model", "CPU"),
        ("physical_cores", "Physical cores"),
        ("logical_cores", "Logical cores")
    ):
        if key in cpu:
            lines.append(f"{label}: {cpu[key]}")
    if "memory" in portable:
        lines.append(f"Memory: {portable['memory']['total_gb']} GB")
    for gpu in portable.get("gpus", ()):
        lines.append(f"GPU: {gpu['model']}")
        if "vram_gb" in gpu:
            lines.append(f"VRAM: {gpu['vram_gb']} GB")
    return lines


def _local_lines(local):
    lines = []
    connection = local.get("connection", {})
    for key, label in (
        ("hostname", "Hostname"),
        ("tailscale_name", "Tailscale name"),
        ("ssh_user", "SSH user")
    ):
        if key in connection:
            lines.append(f"{label}: {connection[key]}")
    if connection.get("ssh_available"):
        lines.append("SSH: available")
    for user in local.get("users", ()):
        if user.get("role") == "current-user":
            lines.append(f"Current user: {user['username']}")
    if local.get("network"):
        lines.append("Network addresses:")
        lines.extend(
            f"  - {item['interface']}: {item['address']}"
            for item in local["network"]
        )
    for service in local.get("services", ()):
        detail = f"{service['name']}"
        if "port" in service:
            detail += f" ({service['port']}/{service.get('protocol', 'tcp')})"
        lines.append(f"Service: {detail}")
    return lines


def show_inspection(inspection):
    rot_say("Portable machine metadata")
    portable = _portable_lines(inspection.portable)
    rot_continue("\n".join(portable) if portable else "No portable facts detected.")
    rot_say("Local machine metadata")
    local = _local_lines(inspection.local)
    rot_continue("\n".join(local) if local else "No local facts detected.")


def _machine_name(inspection):
    hostname = inspection.local.get("connection", {}).get("hostname")
    if not hostname:
        raise MachineRegistrationError(
            "Could not register this machine because no hostname was detected."
        )
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", hostname).strip("._-")
    if not name:
        raise MachineRegistrationError(
            "Could not derive a valid machine context ID from the hostname."
        )
    try:
        loader.validate_context_name(name)
    except loader.ContextError as error:
        raise MachineRegistrationError(str(error)) from None
    return name


def _available_machine_name(name):
    root = loader.CONTEXT_ROOT / "machines"
    if not os.path.lexists(root / name):
        return name
    index = 2
    while os.path.lexists(root / f"{name}-{index}"):
        index += 1
    return f"{name}-{index}"


def register_local_machine(inspection=None):
    try:
        inspection = inspect_local_machine() if inspection is None else inspection
        show_inspection(inspection)
        name = _available_machine_name(_machine_name(inspection))
        display_name = name.replace("-", " ").replace("_", " ").title()
        destination = machines.create_machine(
            name,
            display_name,
            inspection.portable
        )
        machine = machines.load_machine_context(name)

        try:
            set_local_context_binding("machine", machine.name)
        except ConfigError:
            for filename in machines.PORTABLE_FILENAMES:
                (destination / filename).unlink(missing_ok=True)
            destination.rmdir()
            raise
    except MachineRegistrationError:
        raise
    except Exception as error:
        raise MachineRegistrationError(str(error)) from None

    rot_say(f"Registered machine: {machine.name}")
    rot_say(f"Set as local machine: {machine.name}")
    return machine


def machine_inspect(args):
    try:
        configured = get_local_context_bindings().get("machine")
    except ConfigError as error:
        rot_say(str(error))
        return 2

    if configured is not None:
        try:
            machines.load_machine_context(configured)
        except machines.MachineContextError:
            rot_say(
                f"Configured local machine '{configured}' is unavailable.\n"
                "Inspecting and registering this machine again."
            )
        else:
            try:
                inspection = inspect_local_machine()
                show_inspection(inspection)
            except Exception as error:
                rot_say(f"Could not inspect this machine:\n{error}")
                return 1
            return 0
    else:
        rot_say("No local machine configured.\nInspecting this machine...")

    try:
        register_local_machine()
    except MachineRegistrationError as error:
        rot_say(f"Could not register this machine:\n{error}")
        return 1
    return 0
