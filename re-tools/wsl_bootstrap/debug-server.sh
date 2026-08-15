#!/usr/bin/env bash
set -u
set -o pipefail

# Rot debug server manager.
#
# State-driven single-user experience:
#   - shows status automatically on every render
#   - starts/stops the Rot-managed debugger server(s)
#   - provides an emergency "stop all" that only touches processes
#     whose identity is positively recorded in the state files
#   - survives crashes: state is kept on disk so a later invocation
#     can detect and stop previously launched servers

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
WINDOWS_SCRIPT="$SCRIPT_DIR/debug-server.ps1"

# ---------------------------------------------------------------------------
# Locations
# ---------------------------------------------------------------------------
DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/rot-tools"
STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/rot-tools/debug-server"
BN_LLDB_DIR="$DATA_DIR/debuggers/binary-ninja/linux"

mkdir -p "$STATE_DIR" "$BN_LLDB_DIR" 2>/dev/null || true

LINUX_STATE_FILE="$STATE_DIR/linux.state"
LINUX_LOG_FILE="$STATE_DIR/lldb-server.log"

# ---------------------------------------------------------------------------
# Configuration (environment overrides)
# ---------------------------------------------------------------------------
LINUX_BIND="${ROT_DEBUG_LINUX_BIND:-127.0.0.1}"
LINUX_PORT="${ROT_DEBUG_LINUX_PORT:-31337}"
LINUX_LISTEN="$LINUX_BIND:$LINUX_PORT"

WINDOWS_BIND="${ROT_DEBUG_WINDOWS_BIND:-127.0.0.1}"
WINDOWS_PORT="${ROT_DEBUG_WINDOWS_PORT:-31338}"
WINDOWS_ARCH="${ROT_DEBUG_WINDOWS_ARCH:-amd64}"

# Elevation for package installs. Unset -> "sudo". Set to empty to disable
# (used by the automated test suite with fake package managers).
ROT_SUDO="${ROT_DEBUG_SUDO-sudo}"

# Resolved lldb-server (filled by resolve_lldb_server).
LLDB_PATH=""
LLDB_SOURCE=""

# Linux server status (filled by linux_status).
LINUX_STATUS="none"
LINUX_PID=""
LINUX_EXE=""
LINUX_LISTEN=""

# Windows server status (filled by windows_status).
WIN_STATUS="unavailable"
WIN_PID=""
WIN_LISTEN=""

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
state_get() {
    local file="$1" key="$2" line
    [[ -f "$file" ]] || { printf ''; return; }
    while IFS= read -r line; do
        if [[ "$line" == "$key="* ]]; then
            printf '%s\n' "${line#*=}"
            return
        fi
    done < "$file"
    printf ''
}

is_wsl() {
    grep -qi microsoft /proc/version 2>/dev/null
}

distro_id() {
    local id=""
    if [[ -r /etc/os-release ]]; then
        id="$(sed -n 's/^ID=//p' /etc/os-release | head -n1 | tr -d '"')"
    fi
    printf '%s' "${id:-unknown}"
}

detect_os() {
    local id
    id="$(distro_id)"
    if is_wsl; then
        printf 'WSL (%s)' "$id"
    else
        printf 'Linux (%s)' "$id"
    fi
}

# ---------------------------------------------------------------------------
# Package manager detection
# ---------------------------------------------------------------------------
detect_pkg_manager() {
    if command -v pacman >/dev/null 2>&1; then
        printf 'pacman'
    elif command -v apt-get >/dev/null 2>&1; then
        printf 'apt'
    else
        # Future: dnf, zypper can be added here in this order.
        printf 'unsupported'
    fi
}

# ---------------------------------------------------------------------------
# lldb-server resolution
#
# Order: 1) LLDB_SERVER  2) managed Binary Ninja  3) system PATH
# ---------------------------------------------------------------------------
resolve_lldb_server() {
    LLDB_PATH=""
    LLDB_SOURCE=""

    if [[ -n "${LLDB_SERVER:-}" ]]; then
        if [[ -x "$LLDB_SERVER" ]]; then
            LLDB_PATH="$LLDB_SERVER"
            LLDB_SOURCE="explicit LLDB_SERVER"
            return 0
        fi
        printf 'Warning: LLDB_SERVER=%s is not executable; ignoring.\n' "$LLDB_SERVER" >&2
    fi

    local cand
    for cand in "$BN_LLDB_DIR/lldb-server" \
                "$BN_LLDB_DIR/bin/lldb-server" \
                "$BN_LLDB_DIR/usr/bin/lldb-server"; do
        if [[ -x "$cand" ]]; then
            LLDB_PATH="$cand"
            LLDB_SOURCE="Binary Ninja debugger package"
            return 0
        fi
    done

    if command -v lldb-server >/dev/null 2>&1; then
        LLDB_PATH="$(command -v lldb-server)"
        LLDB_SOURCE="system package"
        return 0
    fi

    return 1
}

# ---------------------------------------------------------------------------
# Process identity validation
#
# A recorded PID is considered Rot-owned only when the live process still
# resolves to the recorded executable (via /proc/<pid>/exe) or its cmdline
# still contains the recorded executable path. Anything else is refused.
# ---------------------------------------------------------------------------
pid_belongs_to_exe() {
    local pid="$1" exe="$2" pexe cmdline
    [[ -r "/proc/$pid/cmdline" ]] || return 1

    pexe="$(readlink "/proc/$pid/exe" 2>/dev/null || true)"
    if [[ -n "$pexe" ]]; then
        if [[ "$(realpath "$exe" 2>/dev/null || printf '%s' "$exe")" == "$pexe" ]]; then
            return 0
        fi
    fi

    cmdline="$(tr '\0' '\n' < "/proc/$pid/cmdline" 2>/dev/null || true)"
    [[ "$cmdline" == *"$exe"* ]]
}

# ---------------------------------------------------------------------------
# Linux server
# ---------------------------------------------------------------------------
linux_status() {
    LINUX_STATUS="none"
    LINUX_PID=""
    LINUX_EXE=""
    LINUX_LISTEN=""

    [[ -f "$LINUX_STATE_FILE" ]] || return 1

    local pid exe listen
    pid="$(state_get "$LINUX_STATE_FILE" pid)"
    exe="$(state_get "$LINUX_STATE_FILE" exe)"
    listen="$(state_get "$LINUX_STATE_FILE" listen)"

    if [[ -z "$pid" || -z "$exe" ]]; then
        rm -f "$LINUX_STATE_FILE"
        LINUX_STATUS="stale"
        return 1
    fi

    if ! kill -0 "$pid" 2>/dev/null; then
        rm -f "$LINUX_STATE_FILE"
        LINUX_STATUS="stale"
        return 1
    fi

    if ! pid_belongs_to_exe "$pid" "$exe"; then
        LINUX_STATUS="unverifiable"
        LINUX_PID="$pid"
        LINUX_EXE="$exe"
        return 2
    fi

    LINUX_STATUS="running"
    LINUX_PID="$pid"
    LINUX_EXE="$exe"
    LINUX_LISTEN="${listen:-$LINUX_BIND:$LINUX_PORT}"
    return 0
}

start_linux() {
    if linux_status; then
        printf 'Linux debug server is already running (PID %s).\n' "$LINUX_PID"
        return 0
    fi

    if [[ "$LINUX_STATUS" == "unverifiable" ]]; then
        printf 'Warning: recorded Linux server (PID %s) could not be verified as Rot-owned.\n' "$LINUX_PID" >&2
        return 1
    fi

    if ! resolve_lldb_server; then
        printf 'Could not find lldb-server.\n' >&2
        printf 'Use "Setup / repair debugger tools" to install it.\n' >&2
        return 1
    fi

    local listen="$LINUX_BIND:$LINUX_PORT"

    printf 'Starting Linux debug server...\n'
    printf '  Server: %s\n' "$LLDB_PATH"
    printf '  Source: %s\n' "$LLDB_SOURCE"
    printf '  Listen: %s\n' "$listen"

    nohup "$LLDB_PATH" p --server --listen "$listen" >"$LINUX_LOG_FILE" 2>&1 &
    local pid=$!

    sleep 0.5

    if ! kill -0 "$pid" 2>/dev/null; then
        printf 'lldb-server exited during startup.\n' >&2
        if [[ -f "$LINUX_LOG_FILE" ]]; then
            tail -n 20 "$LINUX_LOG_FILE" >&2
        fi
        return 1
    fi

    {
        printf 'backend=linux\n'
        printf 'pid=%s\n' "$pid"
        printf 'exe=%s\n' "$LLDB_PATH"
        printf 'listen=%s\n' "$listen"
        printf 'port=%s\n' "$LINUX_PORT"
        printf 'source=%s\n' "$LLDB_SOURCE"
        printf 'started=%s\n' "$(date +%s)"
    } > "$LINUX_STATE_FILE"

    printf 'Linux debug server started (PID %s).\n' "$pid"
    return 0
}

stop_linux() {
    if ! linux_status; then
        case "$LINUX_STATUS" in
            stale)
                printf 'Linux debug server: stale state removed.\n'
                return 0
                ;;
            unverifiable)
                printf 'Warning: refusing to stop PID %s: process identity could not be verified.\n' "$LINUX_PID" >&2
                return 1
                ;;
        esac
        printf 'Linux debug server is not running.\n'
        return 0
    fi

    printf 'Stopping Linux debug server (PID %s)...\n' "$LINUX_PID"
    kill "$LINUX_PID" 2>/dev/null || true

    local i
    for i in 1 2 3 4 5 6 7 8 9 10; do
        kill -0 "$LINUX_PID" 2>/dev/null || break
        sleep 0.1
    done

    if kill -0 "$LINUX_PID" 2>/dev/null; then
        printf 'Server did not exit cleanly; sending SIGKILL.\n' >&2
        kill -9 "$LINUX_PID" 2>/dev/null || true
        sleep 0.2
    fi

    rm -f "$LINUX_STATE_FILE"
    printf 'Linux debug server stopped.\n'
    return 0
}

# ---------------------------------------------------------------------------
# Windows server (delegated to debug-server.ps1)
# ---------------------------------------------------------------------------
windows_available() {
    command -v powershell.exe >/dev/null 2>&1 &&
        command -v wslpath >/dev/null 2>&1 &&
        [[ -f "$WINDOWS_SCRIPT" ]]
}

run_windows_script() {
    local action="$1"

    if ! windows_available; then
        printf 'Windows debug-server control is unavailable.\n' >&2
        return 1
    fi

    local win_script
    win_script="$(wslpath -w "$WINDOWS_SCRIPT")"

    powershell.exe \
        -NoLogo \
        -NoProfile \
        -ExecutionPolicy Bypass \
        -File "$win_script" \
        -Action "$action" \
        -BindAddress "$WINDOWS_BIND" \
        -Port "$WINDOWS_PORT" \
        -Arch "$WINDOWS_ARCH"
}

windows_status() {
    WIN_STATUS="unavailable"
    WIN_PID=""
    WIN_LISTEN=""

    if ! windows_available; then
        return 1
    fi

    local out
    out="$(run_windows_script Status 2>&1 || true)"

    if printf '%s' "$out" | grep -q 'Windows debug server: running'; then
        WIN_STATUS="running"
        WIN_PID="$(printf '%s' "$out" | sed -n 's/^[[:space:]]*PID:[[:space:]]*//p' | head -n1)"
        WIN_LISTEN="$(printf '%s' "$out" | sed -n 's/^[[:space:]]*Listen:[[:space:]]*//p' | head -n1)"
        return 0
    fi

    WIN_STATUS="stopped"
    return 0
}

start_windows() {
    if ! windows_available; then
        printf 'Windows debug server is unavailable from this environment.\n' >&2
        return 1
    fi
    run_windows_script Start
}

stop_windows() {
    if ! windows_available; then
        printf 'Windows debug server is unavailable from this environment.\n' >&2
        return 1
    fi
    run_windows_script Stop
}

# ---------------------------------------------------------------------------
# Setup / repair debugger tools
# ---------------------------------------------------------------------------
setup_tools() {
    printf '\nSetup / repair debugger tools\n'

    if resolve_lldb_server; then
        printf 'lldb-server already available:\n'
        printf '  %s\n' "$LLDB_PATH"
        printf '  source: %s\n' "$LLDB_SOURCE"
        return 0
    fi

    local pm
    pm="$(detect_pkg_manager)"
    printf 'lldb-server is missing. Installing via package manager: %s\n' "$pm"

    local -a elevate=()
    if [[ -n "$ROT_SUDO" && "$(id -u)" -ne 0 ]]; then
        elevate=( "$ROT_SUDO" )
    fi

    case "$pm" in
        pacman)
            printf '  %s pacman -S --needed --noconfirm lldb\n' "${elevate[*]:-pacman}"
            "${elevate[@]}" pacman -S --needed --noconfirm lldb
            ;;
        apt)
            printf '  %s apt-get update\n' "${elevate[*]:-apt-get}"
            "${elevate[@]}" apt-get update
            printf '  %s apt-get install -y lldb\n' "${elevate[*]:-apt-get}"
            "${elevate[@]}" apt-get install -y lldb
            ;;
        unsupported)
            printf 'Unsupported package manager. Install lldb manually and re-run setup.\n' >&2
            return 1
            ;;
    esac

    if command -v lldb-server >/dev/null 2>&1; then
        printf 'Verified lldb-server: %s\n' "$(command -v lldb-server)"
        return 0
    fi

    printf 'ERROR: lldb-server was not found after installation.\n' >&2
    printf 'The package manager reported success, but lldb-server is still missing.\n' >&2
    return 1
}

# ---------------------------------------------------------------------------
# Stop server(s)
# ---------------------------------------------------------------------------
stop_server() {
    local did=0

    linux_status
    if [[ "$LINUX_STATUS" == "running" ]]; then
        stop_linux
        did=1
    fi

    windows_status
    if [[ "$WIN_STATUS" == "running" ]]; then
        stop_windows
        did=1
    fi

    [[ "$did" -eq 1 ]] || printf 'No debug server is running.\n'
}

# Emergency: stop every server that rot-tools can positively identify as its own.
stop_all_rot_servers() {
    printf 'Stopping all Rot debug servers...\n'

    local f backend pid exe found=0
    for f in "$STATE_DIR"/*.state; do
        [[ -f "$f" ]] || continue
        found=1

        backend="$(state_get "$f" backend)"
        pid="$(state_get "$f" pid)"
        exe="$(state_get "$f" exe)"

        case "$backend" in
            linux)
                if [[ -z "$pid" || -z "$exe" ]]; then
                    printf 'Removing incomplete state %s\n' "$f"
                    rm -f "$f"
                    continue
                fi
                if ! kill -0 "$pid" 2>/dev/null; then
                    printf 'Removing stale state %s (PID %s not running)\n' "$f" "$pid"
                    rm -f "$f"
                    continue
                fi
                if ! pid_belongs_to_exe "$pid" "$exe"; then
                    printf 'WARNING: PID %s from %s does not match %s; NOT killing it.\n' "$pid" "$f" "$exe" >&2
                    continue
                fi
                printf 'Stopping Rot Linux server (PID %s)\n' "$pid"
                kill "$pid" 2>/dev/null || true
                sleep 0.2
                if kill -0 "$pid" 2>/dev/null; then
                    kill -9 "$pid" 2>/dev/null || true
                fi
                rm -f "$f"
                ;;
            windows)
                # Handled through the PowerShell side below.
                ;;
            *)
                printf 'Unknown backend %s in %s; leaving it alone.\n' "$backend" "$f" >&2
                ;;
        esac
    done

    if windows_available; then
        printf 'Stopping Windows debug server...\n'
        run_windows_script StopAll || true
    fi

    [[ "$found" -eq 0 ]] && printf 'No Rot debug servers recorded.\n'
}

# ---------------------------------------------------------------------------
# Menu
# ---------------------------------------------------------------------------
print_linux_debugger_line() {
    if resolve_lldb_server; then
        printf 'Linux debugger:\n'
        printf '  %s\n' "$LLDB_PATH"
        printf '  source: %s\n' "$LLDB_SOURCE"
    else
        printf 'Linux debugger: missing\n'
    fi
}

print_windows_debugger_line() {
    if windows_available; then
        printf 'Windows debugger: available\n'
    else
        printf 'Windows debugger: unavailable\n'
    fi
}

render_menu() {
    linux_status
    windows_status

    printf '\nRE DEBUG SERVER\n'
    printf '===============\n'

    if [[ "$LINUX_STATUS" == "running" || "$WIN_STATUS" == "running" ]]; then
        printf '\n'
        if [[ "$LINUX_STATUS" == "running" ]]; then
            printf 'Linux debug server\n'
            printf 'Status: RUNNING\n'
            printf 'PID:    %s\n' "$LINUX_PID"
            printf 'Listen: %s\n' "$LINUX_LISTEN"
            printf '\n'
        fi
        if [[ "$WIN_STATUS" == "running" ]]; then
            printf 'Windows debug server\n'
            printf 'Status: RUNNING\n'
            printf 'PID:    %s\n' "$WIN_PID"
            printf 'Listen: %s\n' "$WIN_LISTEN"
            printf '\n'
        fi
        printf '1) Stop server\n'
        printf '2) Stop all Rot debug servers\n'
        printf '3) Exit\n'
        return 0
    fi

    # Nothing is running.
    if [[ "$LINUX_STATUS" == "unverifiable" ]]; then
        printf '\nWarning: recorded Linux server (PID %s) could not be verified.\n' "$LINUX_PID" >&2
    fi

    printf '\n'
    printf 'Environment: %s\n' "$(detect_os)"
    print_linux_debugger_line
    print_windows_debugger_line
    printf '\n'
    printf 'No debug server is running.\n'
    printf '\n'

    if [[ "$LINUX_STATUS" == "unverifiable" ]]; then
        printf '1) Start Linux server\n'
        if windows_available; then
            printf '2) Start Windows server\n'
            printf '3) Setup / repair debugger tools\n'
            printf '4) Stop all Rot debug servers\n'
            printf '5) Exit\n'
        else
            printf '2) Setup / repair debugger tools\n'
            printf '3) Stop all Rot debug servers\n'
            printf '4) Exit\n'
        fi
        return 0
    fi

    printf '1) Start Linux server\n'
    if windows_available; then
        printf '2) Start Windows server\n'
        printf '3) Setup / repair debugger tools\n'
        printf '4) Exit\n'
    else
        printf '2) Setup / repair debugger tools\n'
        printf '3) Exit\n'
    fi
}

handle_idle_choice() {
    local choice="$1"

    if [[ "$LINUX_STATUS" == "unverifiable" ]]; then
        case "$choice" in
            1) start_linux ;;
            2) if windows_available; then start_windows; else setup_tools; fi ;;
            3) if windows_available; then setup_tools; else stop_all_rot_servers; fi ;;
            4) if windows_available; then stop_all_rot_servers; else exit 0; fi ;;
            5) exit 0 ;;
            *) printf 'Invalid selection.\n' ;;
        esac
        return
    fi

    case "$choice" in
        1) start_linux ;;
        2) if windows_available; then start_windows; else setup_tools; fi ;;
        3) if windows_available; then setup_tools; else exit 0; fi ;;
        4) exit 0 ;;
        *) printf 'Invalid selection.\n' ;;
    esac
}

handle_running_choice() {
    case "$1" in
        1) stop_server ;;
        2) stop_all_rot_servers ;;
        3) exit 0 ;;
        *) printf 'Invalid selection.\n' ;;
    esac
}

main_loop() {
    local choice

    while true; do
        linux_status
        windows_status
        render_menu

        if [[ "$LINUX_STATUS" == "running" || "$WIN_STATUS" == "running" ]]; then
            printf '\n'
            read -rp '> ' choice || { printf '\n'; exit 0; }
            handle_running_choice "$choice"
        else
            printf '\n'
            read -rp '> ' choice || { printf '\n'; exit 0; }
            handle_idle_choice "$choice"
        fi
    done
}

main_loop "$@"
