# PowerShell ownership/lifecycle tests for debug-server.ps1
#
# These tests exercise the state file and process identity validation WITHOUT
# launching the real dbgsrv.exe. They run the production script as a child
# PowerShell process against a throwaway state directory.
#
#   * Pure state-logic tests run on any platform (Windows or Linux pwsh).
#   * Live-process identity tests only run on real Windows (they use a
#     renamed cmd.exe standing in for dbgsrv.exe). They are reported as SKIP
#     elsewhere; no real debugging session is ever touched.

$ErrorActionPreference = "Stop"

$ps1 = Join-Path (Split-Path -Parent $PSScriptRoot) "debug-server.ps1"
$stateFileName = "dbgsrv.state"

$pass = 0
$fail = 0
$skip = 0

function Ok   { param([string]$name)    $script:pass++; Write-Host "   ok   $name" }
function Fail { param([string]$name, [string]$detail) $script:fail++; Write-Host "   FAIL $name :: $detail" }
function Skip { param([string]$name)    $script:skip++; Write-Host "   skip $name" }

function Invoke-DbgPs1 {
    param([string]$Action, [switch]$MachineReadable)

    $exe = if ($env:OS -eq "Windows_NT") { "powershell.exe" } else { "pwsh" }
    $args = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $ps1, "-Action", $Action)
    if ($MachineReadable) { $args += "-MachineReadable" }
    # Note: native stderr is deliberately NOT merged (2>&1). Under
    # $ErrorActionPreference=Stop, Windows PowerShell 5.1 turns merged native
    # stderr into terminating errors, which would abort this suite when the
    # child intentionally fails (identity-capture failure test).
    $out = & $exe @args
    return ,$out
}

function Get-StateLine {
    param([object[]]$Lines, [string]$Prefix)
    foreach ($l in $Lines) {
        if ($l -like "$Prefix*") { return $l.Substring($Prefix.Length) }
    }
    return $null
}

function Set-TestState {
    param([int]$ProcessId, [string]$Path, [string]$Started, [string]$Listen = "127.0.0.1:31338")
    Set-Content -LiteralPath $StateFile -Value @(
        "pid=$ProcessId",
        "path=$Path",
        "started=$Started",
        "listen=$Listen"
    )
}

$tempBase = if ($env:TEMP) { $env:TEMP } else { if ($env:TMPDIR) { $env:TMPDIR } else { "/tmp" } }
$StateDir = Join-Path $tempBase ("rot-debug-test-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Force -Path $StateDir | Out-Null
$StateFile = Join-Path $StateDir $stateFileName
$env:ROT_DEBUG_WIN_STATE_DIR = $StateDir

try {
    Write-Host "Running PowerShell ownership test suite"

    # --- missing state ----------------------------------------------------
    $out = Invoke-DbgPs1 -Action Status -MachineReadable
    $missingWarned = $out | Where-Object { $_ -like "*Malformed dbgsrv state removed*" }
    if ((Get-StateLine $out "status=") -eq "stopped" `
        -and -not $missingWarned `
        -and -not (Test-Path -LiteralPath $StateFile)) {
        Ok "missing state reports stopped (no warning, no state file created)"
    } else {
        Fail "missing state reports stopped (no warning, no state file created)" "status=$(Get-StateLine $out 'status=') warned=$([bool]$missingWarned) exists=$(Test-Path -LiteralPath $StateFile)"
    }

    # --- malformed PID ----------------------------------------------------
    Set-Content -LiteralPath $StateFile -Value "pid=not-a-number"
    $out = Invoke-DbgPs1 -Action Status -MachineReadable
    if ((Get-StateLine $out "status=") -eq "stopped" -and -not (Test-Path -LiteralPath $StateFile)) {
        Ok "malformed PID treated as stale and removed"
    } else {
        Fail "malformed PID treated as stale and removed" "status=$(Get-StateLine $out 'status=') exists=$(Test-Path -LiteralPath $StateFile)"
    }

    # --- dead PID ---------------------------------------------------------
    Set-TestState -ProcessId 999999999 -Path "C:\nonexistent\dbgsrv.exe" -Started ((Get-Date).ToString("o"))
    $out = Invoke-DbgPs1 -Action Status -MachineReadable
    if ((Get-StateLine $out "status=") -eq "stopped" -and -not (Test-Path -LiteralPath $StateFile)) {
        Ok "dead PID treated as stale and removed"
    } else {
        Fail "dead PID treated as stale and removed" "status=$(Get-StateLine $out 'status=') exists=$(Test-Path -LiteralPath $StateFile)"
    }

    # --- Probe: dbgsrv.exe resolvable via DBGSRV_PATH ---------------------
    $probePath = Join-Path $StateDir "dbgsrv.probe.exe"
    Set-Content -LiteralPath $probePath -Value "placeholder"
    $oldDbgSrvPath = $env:DBGSRV_PATH
    try {
        $env:DBGSRV_PATH = $probePath
        $out = Invoke-DbgPs1 -Action Probe -MachineReadable
        $avail = Get-StateLine $out "available="
        $gotPath = Get-StateLine $out "path="
        if ($avail -eq "true" -and $gotPath) {
            Ok "Probe reports available with path when dbgsrv.exe resolves"
        } else {
            Fail "Probe reports available with path" "available=$avail path=$gotPath out=$($out -join ' | ')"
        }

        $env:DBGSRV_PATH = Join-Path $StateDir "does-not-exist.exe"
        $out = Invoke-DbgPs1 -Action Probe -MachineReadable
        if ((Get-StateLine $out "available=") -eq "false") {
            Ok "Probe reports unavailable when dbgsrv.exe is missing"
        } else {
            Fail "Probe reports unavailable when dbgsrv.exe is missing" "out=$($out -join ' | ')"
        }
    } finally {
        if ($null -eq $oldDbgSrvPath) { Remove-Item Env:DBGSRV_PATH -ErrorAction SilentlyContinue }
        else { $env:DBGSRV_PATH = $oldDbgSrvPath }
    }

    if ($env:OS -eq "Windows_NT") {
        # --- live PID with wrong identity (this powershell host, name != dbgsrv)
        Set-TestState -ProcessId $PID -Path "C:\nonexistent\dbgsrv.exe" -Started ((Get-Date).ToString("o"))
        $out = Invoke-DbgPs1 -Action Status -MachineReadable
        if ((Get-StateLine $out "status=") -eq "unverifiable") {
            Ok "live PID with wrong identity reported unverifiable"
        } else {
            Fail "live PID with wrong identity reported unverifiable" "status=$(Get-StateLine $out 'status=')"
        }

        # Stop must refuse and must NOT kill the live process or delete state.
        $before = (Get-Process -Id $PID) -ne $null
        $null = Invoke-DbgPs1 -Action Stop | Out-String
        $alive = (Get-Process -Id $PID -ErrorAction SilentlyContinue) -ne $null
        $stateKept = Test-Path -LiteralPath $StateFile
        if ($before -and $alive -and $stateKept) {
            Ok "Stop refuses wrong identity (process alive, state preserved)"
        } else {
            Fail "Stop refuses wrong identity" "alive=$alive stateKept=$stateKept"
        }

        # StopAll must refuse the same way.
        $null = Invoke-DbgPs1 -Action StopAll | Out-String
        $alive = (Get-Process -Id $PID -ErrorAction SilentlyContinue) -ne $null
        $stateKept = Test-Path -LiteralPath $StateFile
        if ($alive -and $stateKept) {
            Ok "StopAll refuses wrong identity (process alive, state preserved)"
        } else {
            Fail "StopAll refuses wrong identity" "alive=$alive stateKept=$stateKept"
        }

        # --- correct dbgsrv identity via a renamed cmd.exe stand-in --------
        $standinLaunched = $false
        try {
            $fakeExe = Join-Path $StateDir "dbgsrv.exe"
            Copy-Item -LiteralPath (Join-Path $env:SystemRoot "System32\cmd.exe") -Destination $fakeExe -Force
            $standin = Start-Process -FilePath $fakeExe -ArgumentList "/c", "ping -n 30 127.0.0.1 > nul" -PassThru
            $standinLaunched = $true
            Start-Sleep -Milliseconds 400

            $cim = Get-CimInstance Win32_Process -Filter "ProcessId = $($standin.Id)"
            if ($cim -and $cim.ExecutablePath -and $cim.CreationDate) {
                Set-TestState -ProcessId $standin.Id -Path $cim.ExecutablePath -Started ([datetime]$cim.CreationDate).ToString("o")
                $out = Invoke-DbgPs1 -Action Status -MachineReadable
                if ((Get-StateLine $out "status=") -eq "running" -and (Get-StateLine $out "pid=") -eq "$($standin.Id)") {
                    Ok "correct dbgsrv identity reported running"
                } else {
                    Fail "correct dbgsrv identity reported running" "status=$(Get-StateLine $out 'status=') pid=$(Get-StateLine $out 'pid=')"
                }

                # Recorded listen value must be reported even when it differs
                # from the current default BindAddress/Port (31338).
                Set-TestState -ProcessId $standin.Id -Path $cim.ExecutablePath -Started ([datetime]$cim.CreationDate).ToString("o") -Listen "127.0.0.1:31999"
                $out = Invoke-DbgPs1 -Action Status -MachineReadable
                if ((Get-StateLine $out "listen=") -eq "127.0.0.1:31999") {
                    Ok "Status reports recorded listen address, not current args"
                } else {
                    Fail "Status reports recorded listen address" "listen=$(Get-StateLine $out 'listen=')"
                }

                $null = Invoke-DbgPs1 -Action Stop | Out-String
                Start-Sleep -Milliseconds 300
                $gone = (Get-Process -Id $standin.Id -ErrorAction SilentlyContinue) -eq $null
                $stateRemoved = -not (Test-Path -LiteralPath $StateFile)
                if ($gone -and $stateRemoved) {
                    Ok "Stop kills verified dbgsrv and removes state"
                } else {
                    Fail "Stop kills verified dbgsrv and removes state" "gone=$gone stateRemoved=$stateRemoved"
                }
            } else {
                Skip "correct dbgsrv identity (could not query stand-in process identity)"
            }
        } catch {
            Skip "correct dbgsrv identity (could not launch stand-in: $($_.Exception.Message))"
        } finally {
            if ($standinLaunched) {
                Stop-Process -Id $standin.Id -Force -ErrorAction SilentlyContinue
                Remove-Item -LiteralPath (Join-Path $StateDir "dbgsrv.exe") -Force -ErrorAction SilentlyContinue
            }
        }

        # --- startup fails safely when process identity cannot be captured ---
        $fakeExe2 = Join-Path $StateDir "dbgsrv.exe"
        Remove-Item -LiteralPath $StateFile -Force -ErrorAction SilentlyContinue
        $oldPath2 = $env:DBGSRV_PATH
        $env:DBGSRV_PATH = $fakeExe2
        $env:ROT_DEBUG_WIN_FORCE_NO_IDENTITY = "1"
        try {
            Copy-Item -LiteralPath (Join-Path $env:SystemRoot "System32\cmd.exe") -Destination $fakeExe2 -Force
            $before = @(Get-Process -Name "dbgsrv" -ErrorAction SilentlyContinue).Count
            $null = Invoke-DbgPs1 -Action Start | Out-String
            $exitCode = $LASTEXITCODE
            Start-Sleep -Milliseconds 300
            $after = @(Get-Process -Name "dbgsrv" -ErrorAction SilentlyContinue).Count
            $stateCreated = Test-Path -LiteralPath $StateFile
            if ($exitCode -ne 0 -and $after -eq $before -and -not $stateCreated) {
                Ok "startup fails safely when identity capture fails (process cleaned up, no state)"
            } else {
                Fail "startup fails safely when identity capture fails" "exit=$exitCode before=$before after=$after stateCreated=$stateCreated"
            }
        } catch {
            Skip "identity-capture failure (could not launch stand-in: $($_.Exception.Message))"
        } finally {
            Remove-Item Env:ROT_DEBUG_WIN_FORCE_NO_IDENTITY -ErrorAction SilentlyContinue
            if ($null -eq $oldPath2) { Remove-Item Env:DBGSRV_PATH -ErrorAction SilentlyContinue }
            else { $env:DBGSRV_PATH = $oldPath2 }
            Remove-Item -LiteralPath $fakeExe2 -Force -ErrorAction SilentlyContinue
            Remove-Item -LiteralPath $StateFile -Force -ErrorAction SilentlyContinue
        }

        # --- Install: no-op when dbgsrv.exe already resolves -----------------
        $placeholder = Join-Path $StateDir "dbgsrv.placeholder.exe"
        Set-Content -LiteralPath $placeholder -Value "placeholder"
        $oldDbgPath = $env:DBGSRV_PATH
        $oldInstallExe = $env:ROT_DEBUG_WIN_INSTALL_EXE
        try {
            $env:DBGSRV_PATH = $placeholder
            # Point the installer at a non-existent file: if Install tried to run
            # it, the run would fail and report installed=false.
            $env:ROT_DEBUG_WIN_INSTALL_EXE = Join-Path $StateDir "must-not-run.exe"
            $out = Invoke-DbgPs1 -Action Install -MachineReadable
            $exitCode = $LASTEXITCODE
            if ((Get-StateLine $out "installed=") -eq "true" -and (Get-StateLine $out "path=") -eq $placeholder -and $exitCode -eq 0) {
                Ok "Install is a no-op when dbgsrv.exe already resolves"
            } else {
                Fail "Install is a no-op when dbgsrv.exe already resolves" "installed=$(Get-StateLine $out 'installed=') exit=$exitCode out=$($out -join ' | ')"
            }
        } finally {
            if ($null -eq $oldDbgPath) { Remove-Item Env:DBGSRV_PATH -ErrorAction SilentlyContinue }
            else { $env:DBGSRV_PATH = $oldDbgPath }
            if ($null -eq $oldInstallExe) { Remove-Item Env:ROT_DEBUG_WIN_INSTALL_EXE -ErrorAction SilentlyContinue }
            else { $env:ROT_DEBUG_WIN_INSTALL_EXE = $oldInstallExe }
            Remove-Item -LiteralPath $placeholder -Force -ErrorAction SilentlyContinue
        }

        # --- Install: installer exit failure => setup failure -----------------
        # DBGSRV_PATH is pinned to a nonexistent file so the "dbgsrv is missing"
        # probe-first branch is exercised deterministically, even on machines
        # that already have Debugging Tools installed.
        $missingDbg = Join-Path $StateDir "dbgsrv.does-not-exist.exe"
        $fakeInstaller = Join-Path $StateDir "fake-winsdk.cmd"
        Set-Content -LiteralPath $fakeInstaller -Value @("@echo off", "exit /b 5")
        $oldDbgPath2 = $env:DBGSRV_PATH
        $oldInstallExe2 = $env:ROT_DEBUG_WIN_INSTALL_EXE
        try {
            $env:DBGSRV_PATH = $missingDbg
            $env:ROT_DEBUG_WIN_INSTALL_EXE = $fakeInstaller
            $out = Invoke-DbgPs1 -Action Install -MachineReadable
            $exitCode = $LASTEXITCODE
            if ((Get-StateLine $out "installed=") -eq "false" -and (Get-StateLine $out "error=") -eq "installer-failed" -and $exitCode -ne 0) {
                Ok "Install fails when the installer returns failure"
            } else {
                Fail "Install fails when the installer returns failure" "installed=$(Get-StateLine $out 'installed=') error=$(Get-StateLine $out 'error=') exit=$exitCode out=$($out -join ' | ')"
            }
        } finally {
            if ($null -eq $oldDbgPath2) { Remove-Item Env:DBGSRV_PATH -ErrorAction SilentlyContinue }
            else { $env:DBGSRV_PATH = $oldDbgPath2 }
            if ($null -eq $oldInstallExe2) { Remove-Item Env:ROT_DEBUG_WIN_INSTALL_EXE -ErrorAction SilentlyContinue }
            else { $env:ROT_DEBUG_WIN_INSTALL_EXE = $oldInstallExe2 }
            Remove-Item -LiteralPath $fakeInstaller -Force -ErrorAction SilentlyContinue
        }

        # --- Install: exit code 0 is not success if dbgsrv.exe stays missing --
        $fakeInstaller2 = Join-Path $StateDir "fake-winsdk-ok.cmd"
        Set-Content -LiteralPath $fakeInstaller2 -Value @("@echo off", "exit /b 0")
        $oldDbgPath3 = $env:DBGSRV_PATH
        $oldInstallExe3 = $env:ROT_DEBUG_WIN_INSTALL_EXE
        try {
            $env:DBGSRV_PATH = $missingDbg
            $env:ROT_DEBUG_WIN_INSTALL_EXE = $fakeInstaller2
            $out = Invoke-DbgPs1 -Action Install -MachineReadable
            $exitCode = $LASTEXITCODE
            if ((Get-StateLine $out "installed=") -eq "false" -and (Get-StateLine $out "error=") -eq "dbgsrv-not-found-after-install" -and $exitCode -ne 0) {
                Ok "Install fails when installer exits 0 but dbgsrv.exe stays missing"
            } else {
                Fail "Install fails when installer exits 0 but dbgsrv.exe stays missing" "installed=$(Get-StateLine $out 'installed=') error=$(Get-StateLine $out 'error=') exit=$exitCode out=$($out -join ' | ')"
            }
        } finally {
            if ($null -eq $oldDbgPath3) { Remove-Item Env:DBGSRV_PATH -ErrorAction SilentlyContinue }
            else { $env:DBGSRV_PATH = $oldDbgPath3 }
            if ($null -eq $oldInstallExe3) { Remove-Item Env:ROT_DEBUG_WIN_INSTALL_EXE -ErrorAction SilentlyContinue }
            else { $env:ROT_DEBUG_WIN_INSTALL_EXE = $oldInstallExe3 }
            Remove-Item -LiteralPath $fakeInstaller2 -Force -ErrorAction SilentlyContinue
        }
    } else {
        Skip "live-process identity tests (Windows only)"
    }

    Write-Host ""
    Write-Host "Ownership test results: $pass passed, $fail failed, $skip skipped"
    if ($fail -gt 0) {
        exit 1
    }
    exit 0
} finally {
    Remove-Item -LiteralPath $StateDir -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item Env:ROT_DEBUG_WIN_STATE_DIR -ErrorAction SilentlyContinue
}
