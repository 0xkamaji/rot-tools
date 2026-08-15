param(
    [ValidateSet("Start", "Stop", "Status", "StopAll")]
    [string]$Action = "Status",

    [string]$BindAddress = "127.0.0.1",

    [int]$Port = 31338,

    [ValidateSet("amd64", "x86")]
    [string]$Arch = "amd64"
)

$ErrorActionPreference = "Stop"

# Windows per-user state location.
$StateDir = Join-Path $env:LOCALAPPDATA "rot-tools\debug-server"
$PidFile = Join-Path $StateDir "dbgsrv.pid"

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


# Returns the recorded process only when its identity is positively verified
# as dbgsrv.exe. Stale or unverifiable state is handled per the rules below.
function Get-RecordedProcess {
    if (-not (Test-Path -LiteralPath $PidFile -PathType Leaf)) {
        return $null
    }

    $text = (Get-Content -LiteralPath $PidFile -Raw).Trim()

    $pidValue = 0
    if (-not [int]::TryParse($text, [ref]$pidValue)) {
        Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
        return $null
    }

    $process = Get-Process -Id $pidValue -ErrorAction SilentlyContinue
    if (-not $process) {
        Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
        return $null
    }

    # Identity check: the recorded PID must still be dbgsrv.exe.
    if ($process.ProcessName -ne $DbgSrvName) {
        Write-Warning "Recorded PID $pidValue is $($process.ProcessName), not dbgsrv.exe. Refusing to act on it."
        return $null
    }

    return $process
}


function Show-Status {
    $process = Get-RecordedProcess

    if ($process) {
        Write-Host "Windows debug server: running"
        Write-Host "  PID:    $($process.Id)"
        Write-Host "  Listen: ${BindAddress}:$Port"
        Write-Host "  Arch:   $Arch"
        return
    }

    Write-Host "Windows debug server: stopped"
}


function Start-DebugServer {
    $existing = Get-RecordedProcess

    if ($existing) {
        Write-Host "Windows debug server is already running."
        Write-Host "  PID: $($existing.Id)"
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

    Set-Content `
        -LiteralPath $PidFile `
        -Value $process.Id `
        -NoNewline

    Write-Host ""
    Write-Host "Windows debug server started."
    Write-Host "  PID:    $($process.Id)"
    Write-Host "  Listen: ${BindAddress}:$Port"
}


function Stop-DebugServer {
    $process = Get-RecordedProcess

    if (-not $process) {
        Write-Host "Windows debug server is not running."
        return
    }

    Write-Host "Stopping Windows debug server (PID $($process.Id))..."

    Stop-Process -Id $process.Id -ErrorAction Stop
    Wait-Process -Id $process.Id -ErrorAction SilentlyContinue

    Remove-Item `
        -LiteralPath $PidFile `
        -Force `
        -ErrorAction SilentlyContinue

    Write-Host "Windows debug server stopped."
}


switch ($Action) {
    "Start" {
        Start-DebugServer
    }

    "Stop" {
        Stop-DebugServer
    }

    "StopAll" {
        Stop-DebugServer
    }

    "Status" {
        Show-Status
    }
}
