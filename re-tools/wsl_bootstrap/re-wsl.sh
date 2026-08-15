#!/usr/bin/env bash
set -euo pipefail

# RE WSL helper
#
# Menu:
#   1) Install/bootstrap the WSL RE environment
#   2) Debug server: launch debug-server.sh (persistent lldb-server / dbgsrv)
#   3) Show OpenCode MCP setup command
#   4) Exit
#
# The bootstrap deliberately does NOT write OpenCode's MCP config.
# It launches OpenCode's own `mcp add` wizard instead.
#
# Debugging uses the persistent debugger-server workflow in debug-server.sh,
# not the old one-shot gdbserver GDB RSP flow.

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
MCP_COMMAND='npx -y binary-ninja-mcp --host localhost --port 9009'

log()  { printf '\n[*] %s\n' "$*"; }
ok()   { printf '[+] %s\n' "$*"; }
warn() { printf '[!] %s\n' "$*"; }
die()  { printf '[x] %s\n' "$*" >&2; exit 1; }

is_wsl() {
    grep -qi microsoft /proc/version 2>/dev/null
}

load_nvm() {
    export NVM_DIR="$HOME/.nvm"
    if [ -s "$NVM_DIR/nvm.sh" ]; then
        # shellcheck disable=SC1090
        . "$NVM_DIR/nvm.sh"
    fi
}

configure_mirrored_networking() {
    log "Configuring WSL mirrored networking"

    command -v powershell.exe >/dev/null 2>&1 || {
        warn "powershell.exe is unavailable from WSL; skipping .wslconfig setup."
        return
    }

    local win_profile_win win_profile wslconfig
    win_profile_win="$(
        powershell.exe -NoProfile -Command '[Environment]::GetFolderPath("UserProfile")' |
        tr -d '\r'
    )"
    win_profile="$(wslpath "$win_profile_win")"
    wslconfig="$win_profile/.wslconfig"

    # Preserve the rest of .wslconfig and only add/update networkingMode.
    python3 - "$wslconfig" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8") if path.exists() else ""
lines = text.splitlines()

out = []
in_wsl2 = False
saw_wsl2 = False
networking_written = False

for line in lines:
    stripped = line.strip()

    if stripped.startswith("[") and stripped.endswith("]"):
        if in_wsl2 and not networking_written:
            out.append("networkingMode=mirrored")

        in_wsl2 = stripped.lower() == "[wsl2]"
        if in_wsl2:
            saw_wsl2 = True
            networking_written = False

        out.append(line)
        continue

    if in_wsl2 and stripped.lower().startswith("networkingmode="):
        out.append("networkingMode=mirrored")
        networking_written = True
    else:
        out.append(line)

if in_wsl2 and not networking_written:
    out.append("networkingMode=mirrored")

if not saw_wsl2:
    if out and out[-1].strip():
        out.append("")
    out.extend([
        "[wsl2]",
        "networkingMode=mirrored",
    ])

path.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")
PY

    ok "Mirrored networking configured in: $wslconfig"
    warn "If this changed .wslconfig, run 'wsl --shutdown' from Windows PowerShell before testing localhost access."
}

bootstrap() {
    is_wsl || die "Run the bootstrap from inside WSL."

    log "Installing base packages"
    sudo apt update
    sudo apt install -y \
        curl \
        ca-certificates \
        python3 \
        git

    load_nvm

    if ! command -v nvm >/dev/null 2>&1; then
        log "Installing NVM"
        curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.6/install.sh | bash
        load_nvm
    else
        ok "NVM already installed"
    fi

    if ! command -v node >/dev/null 2>&1; then
        log "Installing Node.js LTS (includes npm + npx)"
        nvm install --lts
    else
        ok "Node already installed: $(node --version)"
    fi

    # Keep an LTS Node as the default for future shells.
    nvm install --lts >/dev/null
    nvm alias default 'lts/*' >/dev/null
    nvm use --lts >/dev/null

    export PATH="$HOME/.opencode/bin:$PATH"

    if ! command -v opencode >/dev/null 2>&1; then
        log "Installing OpenCode"
        curl -fsSL https://opencode.ai/install | bash
        export PATH="$HOME/.opencode/bin:$PATH"
    else
        ok "OpenCode already installed: $(opencode --version)"
    fi

    configure_mirrored_networking

    log "Installed versions"
    printf 'Node:      %s\n' "$(node --version)"
    printf 'npm:       %s\n' "$(npm --version)"
    printf 'npx:       %s\n' "$(npx --version)"
    printf 'OpenCode:  %s\n' "$(opencode --version)"

    echo
    echo "Binary Ninja MCP setup"
    echo "----------------------"
    echo "OpenCode will configure the MCP itself."
    echo
    echo "In the wizard choose:"
    echo "  Type:    Local"
    echo "  Name:    binary-ninja"
    echo "  Command: $MCP_COMMAND"
    echo

    read -r -p "Launch 'opencode mcp add' now? [Y/n] " answer
    case "${answer:-Y}" in
        [Yy]|[Yy][Ee][Ss])
            opencode mcp add
            echo
            log "OpenCode MCP status"
            opencode mcp list || true
            ;;
        *)
            warn "Skipped MCP wizard."
            echo "Run it later with:"
            echo "  opencode mcp add"
            echo
            echo "Command field:"
            echo "  $MCP_COMMAND"
            ;;
    esac

    echo
    ok "Bootstrap finished."
    echo
    echo "After a WSL networking change:"
    echo "  Windows PowerShell> wsl --shutdown"
    echo
    echo "For Binary Ninja MCP:"
    echo "  Binary Ninja -> Plugins -> Start MCP Server"
    echo
    echo "Test from WSL:"
    echo "  curl http://localhost:9009/binaries"
    echo "  opencode mcp list"
}

debug_server() {
    is_wsl || die "Run the debug server from inside WSL."

    local dbg="$SCRIPT_DIR/debug-server.sh"
    if [[ -f "$dbg" ]]; then
        echo
        echo "Launching the persistent Rot debug server menu."
        echo "  Linux:   lldb-server (persistent, default port 31337)"
        echo "  Windows: dbgsrv.exe via PowerShell interop (default port 31338)"
        echo
        exec "$dbg"
    fi

    warn "debug-server.sh was not found next to this script."
    echo "Run it from its own location:"
    echo "  ./debug-server.sh"
}

show_menu() {
    clear 2>/dev/null || true
    cat <<'EOF'
RE WSL
======

1) Install / bootstrap RE environment
2) Debug server (debug-server.sh)
3) Show MCP setup command
4) Exit

EOF
}

show_mcp_command() {
    echo
    echo "OpenCode MCP wizard:"
    echo "  opencode mcp add"
    echo
    echo "Choose:"
    echo "  Type:    Local"
    echo "  Name:    binary-ninja"
    echo "  Command: $MCP_COMMAND"
    echo
}

main() {
    while true; do
        show_menu
        read -r -p "Choose [1-4]: " choice

        case "$choice" in
            1)
                bootstrap
                echo
                read -r -p "Press Enter to return to menu..." _
                ;;
            2)
                debug_server
                echo
                read -r -p "Press Enter to return to menu..." _
                ;;
            3)
                show_mcp_command
                echo
                read -r -p "Press Enter to return to menu..." _
                ;;
            4)
                exit 0
                ;;
            *)
                warn "Invalid choice."
                sleep 1
                ;;
        esac
    done
}

main "$@"
