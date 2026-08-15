#!/usr/bin/env bash
set -u

# Test suite for debug-server.sh
#
# Uses temporary XDG_DATA_HOME / XDG_STATE_HOME and fake tools
# (pacman, apt-get, lldb-server, powershell.exe, wslpath) so the
# real machine is never modified.

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
DEBUG_SERVER="$SCRIPT_DIR/../debug-server.sh"
DEBUG_PS1="$SCRIPT_DIR/../debug-server.ps1"

PASS=0
FAIL=0
FAILED_TESTS=""

say()   { printf '\n== %s\n' "$*"; }
ok()    { PASS=$((PASS+1)); printf '   ok   %s\n' "$*"; }
fail()  { FAIL=$((FAIL+1)); FAILED_TESTS="$FAILED_TESTS $*"; printf '   FAIL %s\n' "$*"; }

# ---------------------------------------------------------------------------
# Test harness helpers
# ---------------------------------------------------------------------------
# Create a fresh sandbox with fake tooling.
new_sandbox() {
    TMP="$(mktemp -d)"
    XDG_DATA="$TMP/data"
    XDG_STATE="$TMP/state"
    BIN="$TMP/bin"
    mkdir -p "$XDG_DATA" "$XDG_STATE" "$BIN"
}

destroy_sandbox() {
    # Kill any fake lldb-server processes we may have left behind.
    if [[ -n "${PID1:-}" ]]; then
        kill -9 "$PID1" 2>/dev/null
        wait "$PID1" 2>/dev/null
    fi
    if [[ -n "${PID2:-}" ]]; then
        kill -9 "$PID2" 2>/dev/null
        wait "$PID2" 2>/dev/null
    fi
    PID1=""; PID2=""
    rm -rf "$TMP"
}

# Write a fake executable.
make_fake() {
    local path="$1"
    printf '#!/usr/bin/env bash\n%s\n' "$2" > "$path"
    chmod +x "$path"
}

# Build the standard fake toolset. `has_lldb` controls whether a fake
# lldb-server lands on PATH. `pm` selects which package manager fakes to
# install (pacman | apt | both).
install_fakes() {
    local has_lldb="$1" pm="$2"

    # A fake lldb-server that stays alive and accepts any args.
    make_fake "$BIN/lldb-server" '
while true; do sleep 1; done
'
    if [[ "$has_lldb" != "yes" ]]; then
        rm -f "$BIN/lldb-server"
    fi

    if [[ "$pm" == "pacman" || "$pm" == "both" ]]; then
        make_fake "$BIN/pacman" '
printf "PACMAN:%s\n" "$*" >> "$FAKE_LOG"
exit "${PACMAN_EXIT:-0}"
'
    fi

    if [[ "$pm" == "apt" || "$pm" == "both" ]]; then
        make_fake "$BIN/apt-get" '
printf "APTGET:%s\n" "$*" >> "$FAKE_LOG"
exit "${APTGET_EXIT:-0}"
'
    fi

    # Fake Windows interop so the real powershell.exe is never reached.
    make_fake "$BIN/powershell.exe" '
printf "POWERSHELL:%s\n" "$*" >> "$FAKE_LOG"
case " $* " in
    *" -Action Status "*)
        printf "Windows debug server: stopped\n"
        ;;
    *" -Action StopAll "* | *" -Action Stop "*)
        printf "Windows debug server stopped.\n"
        ;;
    *" -Action Start "*)
        printf "Windows debug server started.\n"
        ;;
esac
'
    make_fake "$BIN/wslpath" '
printf "%s\n" "Z:\\\\fake\\\\debug-server.ps1"
'
}

# Run the debug server script non-interactively with given stdin.
run_script() {
    local stdin="$1"
    shift
    # shellcheck disable=SC2034
    LLDB_SERVER="${LLDB_SERVER:-}" \
    XDG_DATA_HOME="$XDG_DATA" \
    XDG_STATE_HOME="$XDG_STATE" \
    FAKE_LOG="$TMP/fake.log" \
    ROT_DEBUG_SUDO="" \
    PATH="$BIN:$PATH" \
    bash "$DEBUG_SERVER" <<< "$stdin" "$@"
}

# ---------------------------------------------------------------------------
# 1. Bash syntax
# ---------------------------------------------------------------------------
test_bash_syntax() {
    say "bash -n $DEBUG_SERVER"
    if bash -n "$DEBUG_SERVER" 2>"$TMP/syntax.err"; then
        ok "bash syntax"
    else
        fail "bash syntax: $(cat "$TMP/syntax.err")"
    fi
}

# ---------------------------------------------------------------------------
# 2. PowerShell syntax (only when PowerShell available)
# ---------------------------------------------------------------------------
test_pwsh_syntax() {
    if command -v pwsh >/dev/null 2>&1; then
        say "pwsh syntax $DEBUG_PS1"
        if pwsh -NoProfile -Command "
            \$errs = \$null
            \$null = [System.Management.Automation.PSParser]::Tokenize(
                (Get-Content -Raw '$DEBUG_PS1'), [ref]\$errs)
            if (\$errs.Count -gt 0) { \$errs | ForEach-Object { \$_.Message }; exit 1 }
        " 2>"$TMP/pwsh.err"; then
            ok "PowerShell syntax"
        else
            fail "PowerShell syntax: $(cat "$TMP/pwsh.err")"
        fi
    elif command -v powershell.exe >/dev/null 2>&1 && command -v wslpath >/dev/null 2>&1; then
        say "powershell.exe syntax $DEBUG_PS1 (read-only Tokenize)"
        local win_script
        win_script="$(wslpath -w "$DEBUG_PS1")"
        if powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "
            \$errs = \$null
            \$null = [System.Management.Automation.PSParser]::Tokenize(
                (Get-Content -Raw '$win_script'), [ref]\$errs)
            if (\$errs.Count -gt 0) { \$errs | ForEach-Object { \$_.Message }; exit 1 }
        " 2>"$TMP/pwsh.err"; then
            ok "PowerShell syntax (powershell.exe)"
        else
            fail "PowerShell syntax: $(cat "$TMP/pwsh.err")"
        fi
    else
        printf '   skip PowerShell syntax (no pwsh / powershell.exe available)\n'
    fi
}

# ---------------------------------------------------------------------------
# 3. Package manager detection
# ---------------------------------------------------------------------------
test_pacman_detection() {
    say "Arch/pacman detection"
    new_sandbox
    install_fakes no pacman
    run_script "3
4
" >"$TMP/out" 2>&1
    if grep -q 'package manager: pacman' "$TMP/out" && grep -q 'PACMAN:-S --needed --noconfirm lldb' "$TMP/fake.log"; then
        ok "pacman selected"
    else
        fail "pacman not selected; log=$(cat "$TMP/fake.log" 2>/dev/null)"
    fi
    destroy_sandbox
}

test_apt_detection() {
    say "Debian/Ubuntu apt detection"
    new_sandbox
    install_fakes no apt
    run_script "3
4
" >"$TMP/out" 2>&1
    if grep -q 'package manager: apt' "$TMP/out" && grep -q 'APTGET:update' "$TMP/fake.log" && grep -q 'APTGET:install -y lldb' "$TMP/fake.log"; then
        ok "apt selected"
    else
        fail "apt not selected; log=$(cat "$TMP/fake.log" 2>/dev/null)"
    fi
    destroy_sandbox
}

# ---------------------------------------------------------------------------
# 4. Already-installed lldb-server skips install
# ---------------------------------------------------------------------------
test_installed_skips_install() {
    say "already-installed lldb-server skips install"
    new_sandbox
    install_fakes yes both
    run_script "3
4
" >"$TMP/out" 2>&1
    if grep -q 'lldb-server already available' "$TMP/out" && ! grep -qE 'PACMAN:|APTGET:' "$TMP/fake.log"; then
        ok "install skipped"
    else
        fail "install not skipped; log=$(cat "$TMP/fake.log" 2>/dev/null)"
    fi
    destroy_sandbox
}

# ---------------------------------------------------------------------------
# 5. Missing lldb-server invokes correct package manager
#    and package-manager success followed by missing lldb-server is failure
# ---------------------------------------------------------------------------
test_missing_invokes_pm() {
    say "missing lldb-server invokes correct package manager"
    new_sandbox
    install_fakes no apt
    run_script "3
4
" >"$TMP/out" 2>&1
    if grep -q 'APTGET:update' "$TMP/fake.log" && grep -q 'APTGET:install -y lldb' "$TMP/fake.log"; then
        ok "apt-get invoked"
    else
        fail "apt-get not invoked; log=$(cat "$TMP/fake.log" 2>/dev/null)"
    fi
    destroy_sandbox
}

test_pm_success_but_missing() {
    say "package-manager success followed by missing lldb-server is failure"
    new_sandbox
    install_fakes no apt
    run_script "3
4
" >"$TMP/out" 2>&1
    if grep -q 'lldb-server was not found after installation' "$TMP/out"; then
        ok "treated as failure"
    else
        fail "did not report failure; out=$(tail -n 20 "$TMP/out")"
    fi
    destroy_sandbox
}

# ---------------------------------------------------------------------------
# 6. lldb-server resolution precedence
# ---------------------------------------------------------------------------
test_explicit_lldb_server_precedence() {
    say "explicit LLDB_SERVER precedence"
    new_sandbox
    install_fakes yes both
    # Extra lldb-server on PATH + explicit override.
    mkdir -p "$BIN/extra"
    cp "$BIN/lldb-server" "$BIN/extra/lldb-server"
    LLDB_SERVER="$BIN/extra/lldb-server" \
    XDG_DATA_HOME="$XDG_DATA" XDG_STATE_HOME="$XDG_STATE" \
    FAKE_LOG="$TMP/fake.log" ROT_DEBUG_SUDO="" \
    PATH="$BIN:$PATH" bash "$DEBUG_SERVER" <<< "1
3
" >"$TMP/out" 2>&1

    local state="$XDG_STATE/rot-tools/debug-server/linux.state"
    if [[ -f "$state" ]] && grep -q 'exe='"$BIN/extra/lldb-server" "$state"; then
        ok "explicit LLDB_SERVER used"
        PID1="$(sed -n 's/^pid=//p' "$state")"
    else
        fail "explicit LLDB_SERVER not used; state=$(cat "$state" 2>/dev/null)"
    fi
    destroy_sandbox
}

test_bn_managed_precedence() {
    say "Binary Ninja managed-server precedence"
    new_sandbox
    install_fakes yes both
    local bn="$XDG_DATA/rot-tools/debuggers/binary-ninja/linux/lldb-server"
    mkdir -p "$(dirname "$bn")"
    cp "$BIN/lldb-server" "$bn"
    run_script "1
3
" >"$TMP/out" 2>&1

    local state="$XDG_STATE/rot-tools/debug-server/linux.state"
    if [[ -f "$state" ]] && grep -q 'exe='"$bn" "$state"; then
        ok "Binary Ninja managed server used"
        PID1="$(sed -n 's/^pid=//p' "$state")"
    else
        fail "BN managed server not used; state=$(cat "$state" 2>/dev/null)"
    fi
    destroy_sandbox
}

test_path_fallback() {
    say "PATH fallback"
    new_sandbox
    install_fakes yes both
    run_script "1
3
" >"$TMP/out" 2>&1

    local state="$XDG_STATE/rot-tools/debug-server/linux.state"
    if [[ -f "$state" ]] && grep -q 'exe='"$BIN/lldb-server" "$state"; then
        ok "PATH lldb-server used"
        PID1="$(sed -n 's/^pid=//p' "$state")"
    else
        fail "PATH lldb-server not used; state=$(cat "$state" 2>/dev/null)"
    fi
    destroy_sandbox
}

# ---------------------------------------------------------------------------
# 7. Start records state
# ---------------------------------------------------------------------------
test_start_records_state() {
    say "start records state"
    new_sandbox
    install_fakes yes both
    run_script "1
3
" >"$TMP/out" 2>&1

    local state="$XDG_STATE/rot-tools/debug-server/linux.state"
    if [[ -f "$state" ]] \
        && grep -q '^backend=linux$' "$state" \
        && grep -q '^listen=127.0.0.1:31337$' "$state" \
        && grep -q '^port=31337$' "$state" \
        && grep -q '^source=system package$' "$state" \
        && grep -q '^pid=[0-9]' "$state"; then
        ok "state recorded"
        PID1="$(sed -n 's/^pid=//p' "$state")"
    else
        fail "state not recorded; state=$(cat "$state" 2>/dev/null)"
    fi
    destroy_sandbox
}

# ---------------------------------------------------------------------------
# 8. Restart detects already-running server
# ---------------------------------------------------------------------------
test_restart_detects_running() {
    say "restart detects already-running server"
    new_sandbox
    install_fakes yes both
    run_script "1
3
" >"$TMP/out1" 2>&1

    local state="$XDG_STATE/rot-tools/debug-server/linux.state"
    PID1="$(sed -n 's/^pid=//p' "$state")"

    run_script "3
" >"$TMP/out2" 2>&1

    if grep -q 'Status: RUNNING' "$TMP/out2" && grep -q "PID:    $PID1" "$TMP/out2"; then
        ok "restart detected running server"
    else
        fail "did not detect running server; out2=$(cat "$TMP/out2")"
    fi
    destroy_sandbox
}

# ---------------------------------------------------------------------------
# 9. Normal stop only stops owned PID
# ---------------------------------------------------------------------------
test_normal_stop_owned_only() {
    say "normal stop only stops owned PID"
    new_sandbox
    install_fakes yes both

    # Start two fake lldb-server processes directly. Record only one.
    "$BIN/lldb-server" >/dev/null 2>&1 &
    PID1=$!
    "$BIN/lldb-server" >/dev/null 2>&1 &
    PID2=$!

    local state="$XDG_STATE/rot-tools/debug-server"
    mkdir -p "$state"
    {
        printf 'backend=linux\n'
        printf 'pid=%s\n' "$PID1"
        printf 'exe=%s\n' "$BIN/lldb-server"
        printf 'listen=127.0.0.1:31337\n'
        printf 'port=31337\n'
        printf 'source=system package\n'
    } > "$state/linux.state"

    run_script "1
4
" >"$TMP/out" 2>&1

    if ! kill -0 "$PID1" 2>/dev/null && kill -0 "$PID2" 2>/dev/null; then
        ok "only owned PID stopped"
    else
        fail "owned PID alive or unowned PID killed"
    fi
    destroy_sandbox
}

# ---------------------------------------------------------------------------
# 10. Stale PID state is cleaned
# ---------------------------------------------------------------------------
test_stale_state_cleaned() {
    say "stale PID state is cleaned"
    new_sandbox
    install_fakes yes both

    local state="$XDG_STATE/rot-tools/debug-server"
    mkdir -p "$state"
    {
        printf 'backend=linux\n'
        printf 'pid=999999\n'
        printf 'exe=%s\n' "$BIN/lldb-server"
        printf 'listen=127.0.0.1:31337\n'
        printf 'port=31337\n'
    } > "$state/linux.state"

    run_script "3
" >"$TMP/out" 2>&1

    if [[ ! -f "$state/linux.state" ]] && grep -q 'No debug server is running' "$TMP/out"; then
        ok "stale state cleaned"
    else
        fail "stale state not cleaned; state=$(cat "$state/linux.state" 2>/dev/null)"
    fi
    destroy_sandbox
}

# ---------------------------------------------------------------------------
# 11. Stop-all handles all recorded Rot-owned servers
# ---------------------------------------------------------------------------
test_stop_all_owned() {
    say "stop-all handles all recorded Rot-owned servers"
    new_sandbox
    install_fakes yes both

    "$BIN/lldb-server" >/dev/null 2>&1 &
    PID1=$!
    "$BIN/lldb-server" >/dev/null 2>&1 &
    PID2=$!

    local state="$XDG_STATE/rot-tools/debug-server"
    mkdir -p "$state"
    for f in linux.state second.state; do
        local pid
        if [[ "$f" == "linux.state" ]]; then pid=$PID1; else pid=$PID2; fi
        {
            printf 'backend=linux\n'
            printf 'pid=%s\n' "$pid"
            printf 'exe=%s\n' "$BIN/lldb-server"
            printf 'listen=127.0.0.1:31337\n'
            printf 'port=31337\n'
        } > "$state/$f"
    done

    run_script "2
4
" >"$TMP/out" 2>&1

    if ! kill -0 "$PID1" 2>/dev/null && ! kill -0 "$PID2" 2>/dev/null \
        && [[ ! -f "$state/linux.state" ]] && [[ ! -f "$state/second.state" ]]; then
        ok "all recorded servers stopped"
    else
        fail "stop-all did not stop all recorded servers"
    fi
    destroy_sandbox
}

# ---------------------------------------------------------------------------
# 12. Stop-all refuses an unverifiable PID
# ---------------------------------------------------------------------------
test_stop_all_unverifiable() {
    say "stop-all refuses an unverifiable PID"
    new_sandbox
    install_fakes yes both

    # A live process that is NOT the recorded lldb-server.
    sleep 1000 &
    PID1=$!

    local state="$XDG_STATE/rot-tools/debug-server"
    mkdir -p "$state"
    {
        printf 'backend=linux\n'
        printf 'pid=%s\n' "$PID1"
        printf 'exe=%s\n' "$BIN/lldb-server"
        printf 'listen=127.0.0.1:31337\n'
        printf 'port=31337\n'
    } > "$state/linux.state"

    run_script "4
4
" >"$TMP/out" 2>&1

    if kill -0 "$PID1" 2>/dev/null && grep -q 'NOT killing it' "$TMP/out"; then
        ok "unverifiable PID not killed"
    else
        fail "unverifiable PID killed or no warning; out=$(cat "$TMP/out")"
    fi
    destroy_sandbox
}

# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------
main() {
    say "Running debug-server test suite"

    new_sandbox
    test_bash_syntax
    test_pwsh_syntax
    destroy_sandbox

    test_pacman_detection
    test_apt_detection
    test_installed_skips_install
    test_missing_invokes_pm
    test_pm_success_but_missing
    test_explicit_lldb_server_precedence
    test_bn_managed_precedence
    test_path_fallback
    test_start_records_state
    test_restart_detects_running
    test_normal_stop_owned_only
    test_stale_state_cleaned
    test_stop_all_owned
    test_stop_all_unverifiable

    printf '\n'
    printf 'Results: %d passed, %d failed\n' "$PASS" "$FAIL"
    if [[ -n "$FAILED_TESTS" ]]; then
        printf 'Failed:%s\n' "$FAILED_TESTS"
        return 1
    fi
    return 0
}

main "$@"
