#!/usr/bin/env bash
set -u

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/rot-tools/debug-server"
mkdir -p "$STATE_DIR"

LINUX_PID_FILE="$STATE_DIR/lldb-server.pid"
LINUX_LOG_FILE="$STATE_DIR/lldb-server.log"

LINUX_BIND="${ROT_DEBUG_LINUX_BIND:-127.0.0.1}"
LINUX_PORT="${ROT_DEBUG_LINUX_PORT:-31337}"

WINDOWS_BIND="${ROT_DEBUG_WINDOWS_BIND:-127.0.0.1}"
WINDOWS_PORT="${ROT_DEBUG_WINDOWS_PORT:-31338}"
WINDOWS_ARCH="${ROT_DEBUG_WINDOWS_ARCH:-amd64}"

WINDOWS_SCRIPT="$SCRIPT_DIR/debug-server.ps1"


find_lldb_server() {
    if [[ -n "${LLDB_SERVER:-}" && -x "${LLDB_SERVER}" ]]; then
        printf '%s\n' "$LLDB_SERVER"
        return 0
    fi

    if [[ -n "${BN_DEBUGGER_LINUX:-}" ]]; then
        local candidate
        candidate="${BN_DEBUGGER_LINUX}/plugins/lldb/lldb-server"

        if [[ -x "$candidate" ]]; then
            printf '%s\n' "$candidate"
            return 0
        fi
    fi

    if command -v lldb-server >/dev/null 2>&1; then
        command -v lldb-server
        return 0
    fi

    return 1
}


linux_status() {
    if [[ ! -f "$LINUX_PID_FILE" ]]; then
        echo "Linux debug server: stopped"
        return 1
    fi

    local pid
    pid="$(cat "$LINUX_PID_FILE" 2>/dev/null || true)"

    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
        echo "Linux debug server: running"
        echo "  PID:    $pid"
        echo "  Listen: $LINUX_BIND:$LINUX_PORT"
        echo "  Log:    $LINUX_LOG_FILE"
        return 0
    fi

    rm -f "$LINUX_PID_FILE"
    echo "Linux debug server: stopped"
    return 1
}


start_linux() {
    if linux_status >/dev/null 2>&1; then
        linux_status
        return 0
    fi

    local server

    if ! server="$(find_lldb_server)"; then
        echo "Could not find lldb-server."
        echo
        echo "Either put lldb-server in PATH or set:"
        echo
        echo "  LLDB_SERVER=/path/to/lldb-server"
        echo
        echo "For Binary Ninja's debugger-linux package you can instead set:"
        echo
        echo "  BN_DEBUGGER_LINUX=/path/to/debugger-linux"
        return 1
    fi

    echo "Starting Linux debug server..."
    echo "  Server: $server"
    echo "  Listen: $LINUX_BIND:$LINUX_PORT"

    nohup "$server" \
        p \
        --server \
        --listen "$LINUX_BIND:$LINUX_PORT" \
        >"$LINUX_LOG_FILE" 2>&1 &

    local pid=$!

    sleep 0.5

    if ! kill -0 "$pid" 2>/dev/null; then
        echo "lldb-server exited during startup."
        echo
        if [[ -f "$LINUX_LOG_FILE" ]]; then
            tail -n 20 "$LINUX_LOG_FILE"
        fi
        return 1
    fi

    printf '%s\n' "$pid" >"$LINUX_PID_FILE"

    echo
    echo "Linux debug server started."
    echo "  PID:    $pid"
    echo "  Listen: $LINUX_BIND:$LINUX_PORT"
    echo "  Log:    $LINUX_LOG_FILE"
}


stop_linux() {
    if [[ ! -f "$LINUX_PID_FILE" ]]; then
        echo "Linux debug server is not running."
        return 0
    fi

    local pid
    pid="$(cat "$LINUX_PID_FILE" 2>/dev/null || true)"

    if [[ -z "$pid" ]]; then
        rm -f "$LINUX_PID_FILE"
        echo "Linux debug server is not running."
        return 0
    fi

    if kill -0 "$pid" 2>/dev/null; then
        echo "Stopping Linux debug server (PID $pid)..."
        kill "$pid" 2>/dev/null || true

        for _ in {1..20}; do
            if ! kill -0 "$pid" 2>/dev/null; then
                break
            fi
            sleep 0.1
        done

        if kill -0 "$pid" 2>/dev/null; then
            echo "Server did not exit cleanly."
            echo "PID $pid was left running."
            return 1
        fi
    fi

    rm -f "$LINUX_PID_FILE"
    echo "Linux debug server stopped."
}


windows_available() {
    command -v powershell.exe >/dev/null 2>&1 &&
        command -v wslpath >/dev/null 2>&1 &&
        [[ -f "$WINDOWS_SCRIPT" ]]
}


run_windows_script() {
    local action="$1"

    if ! windows_available; then
        echo "Windows debug-server control is unavailable."
        echo
        echo "This requires WSL Windows interop and:"
        echo "  $WINDOWS_SCRIPT"
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


show_status() {
    echo
    linux_status || true

    echo

    if windows_available; then
        run_windows_script Status || true
    else
        echo "Windows debug server: unavailable from this environment"
    fi

    echo
}


while true; do
    echo
    echo "DEBUG SERVER"
    echo "============"
    echo "1) Start Linux debug server"
    echo "2) Start Windows debug server"
    echo "3) Status"
    echo "4) Stop Linux debug server"
    echo "5) Stop Windows debug server"
    echo "6) Exit"
    echo

    read -rp "> " choice

    case "$choice" in
        1)
            start_linux
            ;;
        2)
            run_windows_script Start
            ;;
        3)
            show_status
            ;;
        4)
            stop_linux
            ;;
        5)
            run_windows_script Stop
            ;;
        6)
            exit 0
            ;;
        *)
            echo "Invalid selection."
            ;;
    esac
done
