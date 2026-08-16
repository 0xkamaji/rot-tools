#!/usr/bin/env bash
set -u

# Test suite for re-tools/mcp/setup.sh
#
# Uses a FULLY ISOLATED test PATH so the host's real node, npm, npx,
# opencode, curl, powershell.exe, and package managers can never be reached.
# The sandbox bin dir contains fake tools for the scenario under test plus
# symlinks to the safe system utilities the script itself needs.

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SETUP="$SCRIPT_DIR/../setup.sh"
HARNESS_BASH="$(command -v bash)"

PASS=0
FAIL=0
FAILED_TESTS=""

say()   { printf '\n== %s\n' "$*"; }
ok()    { PASS=$((PASS+1)); printf '   ok   %s\n' "$*"; }
fail()  { FAIL=$((FAIL+1)); FAILED_TESTS="$FAILED_TESTS $*"; printf '   FAIL %s\n' "$*"; }

SAFE_TOOLS=(bash grep sed tr head awk dirname readlink realpath date mkdir rm sleep kill id tail python3)

new_sandbox() {
    TMP="$(mktemp -d)"
    BIN="$TMP/bin"
    mkdir -p "$BIN"
    ALL_BINS+=( "$BIN" )

    local t src
    for t in "${SAFE_TOOLS[@]}"; do
        src="$(command -v "$t")"
        if [[ -z "$src" ]]; then
            printf '   ERROR: cannot locate required safe tool %s on host\n' "$t" >&2
            exit 1
        fi
        ln -s "$src" "$BIN/$t"
    done
}

destroy_sandbox() {
    rm -rf "$TMP"
    TMP=""
    BIN=""
}

trap destroy_sandbox EXIT INT TERM

make_fake() {
    local path="$1"
    printf '#!/usr/bin/env bash\n%s\n' "$2" > "$path"
    chmod +x "$path"
}

# Standard fake toolset. `with` selects which fakes land on PATH:
#   all   - node/npm/npx/opencode/curl all present
#   deps  - curl only (no node/npm/npx/opencode)
install_fakes() {
    local with="$1"

    make_fake "$BIN/node" 'printf "v24.0.0\n"'
    make_fake "$BIN/npm" 'printf "10.0.0\n"'
    make_fake "$BIN/npx" 'printf "10.0.0\n"'

    # Fake OpenCode. `list` output is controlled by FAKE_MCP_LIST; the command
    # line is always shown so the "equivalent server under another name" note
    # is exercised.
    make_fake "$BIN/opencode" '
case "$1" in
    mcp)
        if [ "$2" = "list" ]; then
            printf "MCP Servers\n"
            printf "%s\n" "${FAKE_MCP_LIST:-binary-ninja \033[90mconnected}"
            printf "  npx -y binary-ninja-mcp --host localhost --port 9009\n"
        fi
        ;;
    --version)
        printf "1.18.18\n"
        ;;
    *)
        exit 0
        ;;
esac
'

    # Fake curl: FAKE_BN=reachable -> HTTP 200; anything else -> connect refused.
    make_fake "$BIN/curl" '
if [ "${FAKE_BN:-reachable}" = "reachable" ]; then
    printf "200\n"
    exit 0
fi
exit 7
'

    if [[ "$with" == "deps" ]]; then
        rm -f "$BIN/node" "$BIN/npm" "$BIN/npx" "$BIN/opencode"
    fi
}

run_script() {
    local stdin="$1"
    shift
    FAKE_BN="${FAKE_BN:-reachable}" \
    FAKE_MCP_LIST="${FAKE_MCP_LIST:-}" \
    PATH="$BIN" \
    "$HARNESS_BASH" "$SETUP" "$@" <<< "$stdin"
}

# ---------------------------------------------------------------------------
# 1. Bash syntax
# ---------------------------------------------------------------------------
test_bash_syntax() {
    say "bash -n $SETUP"
    if "$HARNESS_BASH" -n "$SETUP" 2>"$TMP/syntax.err"; then
        ok "bash syntax"
    else
        fail "bash syntax: $(cat "$TMP/syntax.err")"
    fi
}

# ---------------------------------------------------------------------------
# 2. NVM must never be used (Node is installed via OS methods only)
# ---------------------------------------------------------------------------
test_no_nvm() {
    say "setup.sh never invokes nvm (no NVM_DIR / nvm.sh / nvm install)"
    if grep -Eq 'nvm install|nvm\.sh|NVM_DIR' "$SETUP"; then
        fail "functional nvm usage found in setup.sh"
    else
        ok "no functional nvm usage"
    fi
}

# ---------------------------------------------------------------------------
# 3. Full status report with everything present
# ---------------------------------------------------------------------------
test_status_all_found() {
    say "status with all dependencies present"
    new_sandbox
    install_fakes all
    FAKE_BN=reachable
    FAKE_MCP_LIST="binary-ninja \033[90mconnected"
    run_script "" status >"$TMP/out" 2>&1

    if grep -q 'Binary Ninja MCP' "$TMP/out" \
        && grep -q 'Node:       found' "$TMP/out" \
        && grep -q 'npm:        found' "$TMP/out" \
        && grep -q 'npx:        found' "$TMP/out" \
        && grep -q 'OpenCode:   found' "$TMP/out" \
        && grep -q 'localhost:9009: reachable' "$TMP/out" \
        && grep -q 'binary-ninja: configured' "$TMP/out" \
        && grep -q 'status: connected' "$TMP/out"; then
        ok "full status report rendered correctly"
    else
        fail "status report wrong; out=$(cat "$TMP/out")"
    fi
    destroy_sandbox
}

# ---------------------------------------------------------------------------
# 4. Status with dependencies missing and endpoint unreachable
# ---------------------------------------------------------------------------
test_status_missing() {
    say "status with missing dependencies and unreachable endpoint"
    new_sandbox
    install_fakes deps
    FAKE_BN=unreachable
    run_script "" status >"$TMP/out" 2>&1

    if grep -q 'Node:       missing' "$TMP/out" \
        && grep -q 'npm:        missing' "$TMP/out" \
        && grep -q 'npx:        missing' "$TMP/out" \
        && grep -q 'OpenCode:   missing' "$TMP/out" \
        && grep -q 'localhost:9009: unreachable' "$TMP/out" \
        && grep -q 'binary-ninja: missing' "$TMP/out" \
        && grep -q 'status: unknown' "$TMP/out"; then
        ok "missing state rendered correctly"
    else
        fail "missing state wrong; out=$(cat "$TMP/out")"
    fi
    destroy_sandbox
}

# ---------------------------------------------------------------------------
# 5. A differently-named server with the same command is not claimed
# ---------------------------------------------------------------------------
test_underscore_server_not_claimed() {
    say "binary_ninja_poncho_mcp is not claimed as the binary-ninja server"
    new_sandbox
    install_fakes all
    FAKE_BN=reachable
    FAKE_MCP_LIST="binary_ninja_poncho_mcp \033[90mconnected"
    run_script "" status >"$TMP/out" 2>&1

    if grep -q 'binary-ninja: missing' "$TMP/out" \
        && grep -q 'status: unknown' "$TMP/out" \
        && grep -q 'an equivalent server using binary-ninja-mcp' "$TMP/out"; then
        ok "underscore server reported as missing with a note"
    else
        fail "underscore server mishandled; out=$(cat "$TMP/out")"
    fi
    destroy_sandbox
}

# ---------------------------------------------------------------------------
# 6. Interactive menu renders the report and honors Exit
# ---------------------------------------------------------------------------
test_menu_exit() {
    say "interactive menu renders report and exits"
    new_sandbox
    install_fakes all
    FAKE_BN=reachable
    FAKE_MCP_LIST="binary-ninja \033[90mconnected"
    run_script "5
" >"$TMP/out" 2>&1
    local rc=$?

    if [[ "$rc" -eq 0 ]] && grep -q 'Binary Ninja MCP' "$TMP/out" \
        && grep -q '5) Exit' "$TMP/out"; then
        ok "menu rendered and exited cleanly"
    else
        fail "menu behavior wrong; rc=$rc out=$(cat "$TMP/out")"
    fi
    destroy_sandbox
}

# ---------------------------------------------------------------------------
# 7. Connection test subcommand
# ---------------------------------------------------------------------------
test_connection_subcommand() {
    say "--test-connection reports reachability"
    new_sandbox
    install_fakes all
    FAKE_BN=reachable
    run_script "" --test-connection >"$TMP/out" 2>&1

    if grep -q 'localhost:9009: reachable' "$TMP/out"; then
        ok "connection test reported reachable"
    else
        fail "connection test wrong; out=$(cat "$TMP/out")"
    fi
    destroy_sandbox
}

# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------
main() {
    say "Running MCP setup test suite"

    new_sandbox
    test_bash_syntax
    test_no_nvm
    destroy_sandbox

    test_status_all_found
    test_status_missing
    test_underscore_server_not_claimed
    test_menu_exit
    test_connection_subcommand

    printf '\n'
    printf 'Results: %d passed, %d failed\n' "$PASS" "$FAIL"
    if [[ -n "$FAILED_TESTS" ]]; then
        printf 'Failed:%s\n' "$FAILED_TESTS"
        return 1
    fi
    return 0
}

main "$@"