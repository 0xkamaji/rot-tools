param(
    [ValidateSet("Start", "Stop", "Status", "StopAll", "Probe")]
    [string]$Action = "Status",

    [string]$BindAddress = "127.0.0.1",

    [int]$Port = 31338,

    [ValidateSet("amd64", "x86")]
    [string]$Arch = "amd64",

    # Emit stable key=value lines instead of human prose for Status.
    [switch]$MachineReadable
)

$ErrorActionPreference = "Stop"

# Windows per-user state location. ROT_DEBUG_WIN_STATE_DIR is a test-only
# override so the automated suite can run against a throwaway directory.
$StateDir = if ($env:ROT_DEBUG_WIN_STATE_DIR) {
    $env:ROT_DEBUG_WIN_STATE_DIR
} else {
    Join-Path $env:LOCALAPPDATA "rot-tools\debug-server"
}
$StateFile = Join-Path $StateDir "dbgsrv.state"

New-Item -ItemType Directory -Force -Path $StateDir | Out-Null

$DbgSrvName = "dbgsrv"


function Resolve-DbgSrv {
    # Explicit path always wins.
    if ($env:DBGSRV_PATH) {
        if (Test-Path -LiteralPath $env:DBGSRV_PATH -PathType Leaf) {
            return (Resolve-Path -LiteralPath $env:DBGSRV_PATH).Path
        }
        throw "DBGSRV_PATH does not exist: $env:DBGSRV_PATH"
    }

    # Root of Binary Ninja's extracted debugger-win32 package.
    if ($env:BN_DEBUGGER_WIN32) {
        $candidate = Join-Path `
            $env:BN_DEBUGGER_WIN32 `
            "plugins\dbgeng\$Arch\dbgsrv.exe"

        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }

    # Allow debugger-win32 to live beside this script without requiring
    # the binary itself to be committed to Git.
    $localCandidate = Join-Path `
        $PSScriptRoot `
        "debugger-win32\plugins\dbgeng\$Arch\dbgsrv.exe"

    if (Test-Path -LiteralPath $localCandidate -PathType Leaf) {
        return (Resolve-Path -LiteralPath $localCandidate).Path
    }

    # Fall back to Microsoft's Windows SDK Debugging Tools installation.
    $sdkArch = if ($Arch -eq "amd64") { "x64" } else { "x86" }

    $programFilesX86 = ${env:ProgramFiles(x86)}

    if ($programFilesX86) {
        $sdkCandidate = Join-Path `
            $programFilesX86 `
            "Windows Kits\10\Debuggers\$sdkArch\dbgsrv.exe"

        if (Test-Path -LiteralPath $sdkCandidate -PathType Leaf) {
            return (Resolve-Path -LiteralPath $sdkCandidate).Path
        }
    }

    throw @"
Could not find dbgsrv.exe.

Set one of:

  DBGSRV_PATH=C:\full\path\to\dbgsrv.exe

or:

  BN_DEBUGGER_WIN32=C:\path\to\debugger-win32

For Binary Ninja's debugger package the expected layout is:

  debugger-win32\plugins\dbgeng\$Arch\dbgsrv.exe
"@
}


function Read-State {
    $state = @{}
    if (Test-Path -LiteralPath $StateFile -PathType Leaf) {
        foreach ($line in Get-Content -LiteralPath $StateFile) {
            if ($line -match '^([^=]+)=(.*)$') {
                $state[$Matches[1]] = $Matches[2]
            }
        }
    }
    return $state
}

function Write-State {
    param(
        [int]$ProcessId,
        [string]$ExecutablePath,
        [string]$Started,
        [string]$Listen
    )
    Set-Content -LiteralPath $StateFile -Value @(
        "pid=$ProcessId",
        "path=$ExecutablePath",
        "started=$Started",
        "listen=$Listen"
    )
}


# Ownerhip validation. Returns a hashtable:
#
#   Status  = 'ok' | 'nostate' | 'stale' | 'mismatch' | 'unverifiable'
#   Process = live System.Diagnostics.Process when found
#   Pid     = recorded/live PID
#
# Rules:
#   * malformed PID or dead PID -> remove stale state ('stale')
#   * live PID whose name/path/start time disagrees with recorded state
#     -> 'mismatch' (state preserved, never killed)
#   * live dbgsrv whose identity cannot be queried (e.g. access denied)
#     -> 'unverifiable' (state preserved, never killed)
#
# The state is only removed for malformed/dead entries; a positive
# identification is required before anything is killed.
function Test-RecordedProcess {
    $state = Read-State

    if (-not $state) {
        return @{ Status = 'nostate'; Pid = 0 }
    }

    $pidValue = 0
    if (-not [int]::TryParse($state['pid'], [ref]$pidValue) -or $pidValue -le 0) {
        Remove-Item -LiteralPath $StateFile -Force -ErrorAction SilentlyContinue
        return @{ Status = 'stale'; Pid = 0 }
    }

    $process = Get-Process -Id $pidValue -ErrorAction SilentlyContinue
    if (-not $process) {
        Remove-Item -LiteralPath $StateFile -Force -ErrorAction SilentlyContinue
        return @{ Status = 'stale'; Pid = $pidValue }
    }

    if ($process.ProcessName -ne $DbgSrvName) {
        return @{ Status = 'mismatch'; Process = $process; Pid = $pidValue }
    }

    # Query the executable path and creation time via WMI/CIM; this works where
    # Process.MainModule is inaccessible (e.g. elevated processes).
    $executablePath = $null
    $creationDate = $null
    try {
        $cim = Get-CimInstance Win32_Process -Filter "ProcessId = $pidValue" -ErrorAction Stop
        if ($cim) {
            $executablePath = $cim.ExecutablePath
            $creationDate = $cim.CreationDate
        }
    } catch {
        $executablePath = $null
        $creationDate = $null
    }

    $recordedPath = $state['path']
    $recordedStarted = $state['started']

    if ($executablePath -and $recordedPath) {
        $pathOk = [string]::Equals(
            ([string]$executablePath).Trim(),
            ([string]$recordedPath).Trim(),
            [System.StringComparison]::OrdinalIgnoreCase)
        if (-not $pathOk) {
            return @{ Status = 'mismatch'; Process = $process; Pid = $pidValue }
        }
    } else {
        # Windows denied the query: do not silently weaken ownership validation.
        return @{ Status = 'unverifiable'; Process = $process; Pid = $pidValue }
    }

    $recordedDate = [datetime]::MinValue
    if ($creationDate -and $recordedStarted -and [datetime]::TryParse($recordedStarted, [ref]$recordedDate)) {
        $deltaSec = [math]::Abs(([datetime]$creationDate - $recordedDate).TotalSeconds)
        if ($deltaSec -gt 2) {
            # A live dbgsrv.exe with a different start time is almost certainly a
            # reused PID; treat it as identity mismatch and refuse to act.
            return @{ Status = 'mismatch'; Process = $process; Pid = $pidValue }
        }
    } else {
        return @{ Status = 'unverifiable'; Process = $process; Pid = $pidValue }
    }

    return @{
        Status  = 'ok'
        Process = $process
        Pid     = $pidValue
        Listen  = $state['listen']
    }
}

# Wraps Test-RecordedProcess with the side effects callers need:
# stale-state cleanup and a warning stream explaining refusals.
function Resolve-RecordedProcess {
    $result = Test-RecordedProcess

    switch ($result.Status) {
        'stale' {
            if ($result.Pid -gt 0) {
                Write-Warning "Recorded PID $($result.Pid) no longer exists; stale state removed."
            } else {
                Write-Warning "Malformed dbgsrv state removed."
            }
        }
        'mismatch' {
            Write-Warning "Recorded PID $($result.Pid) ($($result.Process.ProcessName)) does not match the recorded dbgsrv identity. Refusing to act on it."
        }
        'unverifiable' {
            Write-Warning "PID $($result.Pid) is dbgsrv.exe but its identity could not be verified against recorded state. Refusing to act on it."
        }
    }

    return $result
}


function Show-Status {
    $result = Resolve-RecordedProcess

    if ($MachineReadable) {
        switch ($result.Status) {
            'ok' {
                $listen = if ($result.Listen) { $result.Listen } else { "${BindAddress}:$Port" }
                "status=running"
                "pid=$($result.Process.Id)"
                "listen=$listen"
            }
            'mismatch' {
                "status=unverifiable"
                "pid=$($result.Pid)"
            }
            'unverifiable' {
                "status=unverifiable"
                "pid=$($result.Pid)"
            }
            default {
                "status=stopped"
            }
        }
        return
    }

    switch ($result.Status) {
        'ok' {
            $listen = if ($result.Listen) { $result.Listen } else { "${BindAddress}:$Port" }
            Write-Host "Windows debug server: running"
            Write-Host "  PID:    $($result.Process.Id)"
            Write-Host "  Listen: $listen"
            Write-Host "  Arch:   $Arch"
        }
        'mismatch' {
            Write-Host "Windows debug server: RUNNING (unverifiable)"
            Write-Host "  PID:    $($result.Pid)"
            Write-Host "  WARNING: recorded process identity does not match; refusing to act on it."
        }
        'unverifiable' {
            Write-Host "Windows debug server: RUNNING (unverifiable)"
            Write-Host "  PID:    $($result.Pid)"
            Write-Host "  WARNING: recorded process identity could not be verified; refusing to act on it."
        }
        default {
            Write-Host "Windows debug server: stopped"
        }
    }
}


function Start-DebugServer {
    $existing = Resolve-RecordedProcess

    if ($existing.Status -eq 'ok') {
        Write-Host "Windows debug server is already running."
        Write-Host "  PID: $($existing.Process.Id)"
        return
    }

    if ($existing.Status -eq 'mismatch' -or $existing.Status -eq 'unverifiable') {
        Write-Warning "Refusing to start: PID $($existing.Pid) is live but could not be verified as the recorded dbgsrv. Inspect and remove '$StateFile' manually if you are certain it is stale."
        return
    }

    $dbgSrv = Resolve-DbgSrv

    Write-Host "Starting Windows debug server..."
    Write-Host "  Server: $dbgSrv"
    Write-Host "  Listen: ${BindAddress}:$Port"
    Write-Host "  Arch:   $Arch"

    $arguments = @(
        "-t",
        "tcp:port=$Port,server=$BindAddress"
    )

    $process = Start-Process `
        -FilePath $dbgSrv `
        -ArgumentList $arguments `
        -PassThru

    Start-Sleep -Milliseconds 500

    $running = Get-Process -Id $process.Id -ErrorAction SilentlyContinue

    if (-not $running) {
        throw "dbgsrv.exe exited during startup."
    }

    # Record enough identity to positively recognize this process later. If the
    # executable path or creation time cannot be captured, terminate only the
    # process just launched and fail: ownership state must never be written
    # from a guess.
    #
    # ROT_DEBUG_WIN_FORCE_NO_IDENTITY is a test-only override that skips the
    # CIM query so the fail-safe path can be exercised without a real dbgsrv.
    $executablePath = $null
    $creationDate = $null
    if (-not $env:ROT_DEBUG_WIN_FORCE_NO_IDENTITY) {
        try {
            $cim = Get-CimInstance Win32_Process -Filter "ProcessId = $($process.Id)" -ErrorAction Stop
            if ($cim) {
                $executablePath = $cim.ExecutablePath
                $creationDate = $cim.CreationDate
            }
        } catch {
            $executablePath = $null
            $creationDate = $null
        }
    }
    if (-not $executablePath -or -not $creationDate) {
        Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
        Wait-Process -Id $process.Id -ErrorAction SilentlyContinue
        throw "Could not capture dbgsrv.exe identity (executable path or start time) after launch; terminated the process and wrote no state."
    }
    $startedString = ([datetime]$creationDate).ToString("o")

    Write-State `
        -ProcessId $process.Id `
        -ExecutablePath $executablePath `
        -Started $startedString `
        -Listen "${BindAddress}:$Port"

    Write-Host ""
    Write-Host "Windows debug server started."
    Write-Host "  PID:    $($process.Id)"
    Write-Host "  Listen: ${BindAddress}:$Port"
}


function Stop-DebugServer {
    $result = Resolve-RecordedProcess

    switch ($result.Status) {
        'ok' {
            Write-Host "Stopping Windows debug server (PID $($result.Process.Id))..."

            Stop-Process -Id $result.Process.Id -ErrorAction Stop
            Wait-Process -Id $result.Process.Id -ErrorAction SilentlyContinue

            Remove-Item `
                -LiteralPath $StateFile `
                -Force `
                -ErrorAction SilentlyContinue

            Write-Host "Windows debug server stopped."
        }
        'mismatch' {
            Write-Warning "Refusing to stop PID $($result.Pid): process identity does not match recorded state."
        }
        'unverifiable' {
            Write-Warning "Refusing to stop PID $($result.Pid): process identity could not be verified."
        }
        default {
            Write-Host "Windows debug server is not running."
        }
    }
}


# Read-only check that dbgsrv.exe can be located without starting anything.
# Emits machine-readable lines so a shell can distinguish "Windows interop
# works but dbgsrv.exe is missing" from "dbgsrv.exe is present".
function Probe-DbgSrv {
    try {
        $path = Resolve-DbgSrv
        "available=true"
        "path=$path"
    } catch {
        "available=false"
    }
}


switch ($Action) {
    "Start" {
        Start-DebugServer
    }

    "Stop" {
        Stop-DebugServer
    }

    "StopAll" {
        Write-Host "Stopping all Rot Windows debug servers..."
        Stop-DebugServer
    }

    "Status" {
        Show-Status
    }

    "Probe" {
        Probe-DbgSrv
    }
}
