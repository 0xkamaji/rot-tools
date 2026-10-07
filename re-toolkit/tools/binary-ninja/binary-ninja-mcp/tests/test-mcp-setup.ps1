# Isolated tests for Windows PowerShell 5.1 and PowerShell 7.
$ErrorActionPreference = "Stop"
$setup = Join-Path (Split-Path -Parent $PSScriptRoot) "mcp_configure.ps1"
$savedEnv = @{}
foreach ($name in @("ROT_MCP_NAME", "ROT_MCP_HOST", "ROT_MCP_PORT")) {
    $savedEnv[$name] = [Environment]::GetEnvironmentVariable($name)
    [Environment]::SetEnvironmentVariable($name, $null)
}

function Assert {
    param([bool]$Condition, [string]$Name)
    if (-not $Condition) { throw "FAIL: $Name" }
    Write-Host "   ok   $Name"
}

# Loading through the menu must not invoke any dependency/network/OpenCode scan.
function Read-Host { return "5" }
function Get-Command { throw "Menu unexpectedly scanned commands" }
try {
    $menu = (. $setup -Action menu 6>&1 | Out-String)
    Assert ($menu -match '1\) Setup / repair' -and $menu -notmatch 'Dependencies:') "menu renders without scanning"
    Remove-Item Function:Get-Command
    Assert ($McpName -eq "binary-ninja" -and $BnHost -eq "localhost" -and $BnPort -eq "9009") "unset overrides use unified defaults"

    # All OpenCode calls resolve to mocks, never a real installation.
    function opencode { throw "PowerShell shim selected instead of executable" }
    function opencode.exe {
        $Script:Calls += ,($args -join " ")
        if ($args[0] -eq "--version") { return "1.2.3" }
        if ($args[1] -eq "list") { return $Script:Fixture }
    }
    $Script:Calls = @()
    $Script:Fixture = "MCP Servers`n$([char]27)[90mbinja disconnected`n  npx -y binary-ninja-mcp --host=bn.example --port=9010`n1 server(s)"
    Check-OpencodeMcp
    Assert ($McpConfigured -eq "configured" -and $McpFoundName -eq "binja" -and $McpFoundBackend -eq "binary-ninja-mcp") "backend detection accepts arbitrary names and ANSI output"
    Assert ($McpStatus -eq "disconnected" -and $McpFoundHost -eq "bn.example" -and $McpFoundPort -eq "9010") "disconnected and equals-style endpoint parsed"

    function Test-Tool { return $true }
    function Check-BnEndpoint { $Script:BnStatus = "reachable" }
    function node { return "v24.0.0" }
    $status = (Show-Status 6>&1 | Out-String)
    Assert ($status -match 'found:   yes' -and $status -match 'name:    binja' -and $status -match 'backend: binary-ninja-mcp' -and $status -match 'host:    bn.example' -and $status -match 'port:    9010' -and $status -match 'status:  disconnected') "status reports discovered backend details"
    $repair = (Setup-Tools 6>&1 | Out-String)
    Assert ($repair -notmatch 'Configure it now|is not configured in OpenCode') "repair does not duplicate differently named backend"
    Remove-Item Function:Test-Tool

    $Script:Fixture = "binary-ninja connected`nnpx -y unrelated-backend"
    Check-OpencodeMcp
    Assert ($McpConfigured -eq "missing" -and $McpNote -match 'does not use binary-ninja-mcp') "name alone is not a backend match"

    $Script:Fixture = "binary-ninja disconnected`nnpx -y binary-ninja-mcp --host chosen --port 9010`nother connected`nnpx -y binary-ninja-mcp --host other --port 9011"
    Check-OpencodeMcp
    Assert ($McpFoundName -eq "binary-ninja" -and $McpFoundHost -eq "chosen" -and $McpFoundPort -eq "9010" -and $McpStatus -eq "disconnected") "preferred entry retains its own endpoint and status"
    $Script:Fixture = "first pending`nnpx -y binary-ninja-mcp`nsecond connected`nnpx -y binary-ninja-mcp"
    Check-OpencodeMcp
    Assert ($McpFoundName -eq "first" -and $McpStatus -eq "unknown" -and -not $McpFoundHost) "first backend match used when preferred name absent"
    $Script:Fixture = "inline npx -y binary-ninja-mcp --host local --port 9013 connected"
    Check-OpencodeMcp
    Assert ($McpFoundName -eq "inline" -and $McpStatus -eq "connected" -and $McpFoundPort -eq "9013") "same-line name and command supported"

    # Re-load to exercise environment initialization, still exiting immediately.
    $env:ROT_MCP_NAME = "preferred"
    $env:ROT_MCP_HOST = "custom.example"
    $env:ROT_MCP_PORT = "9014"
    $null = . $setup -Action menu 6>&1
    Assert ($McpName -eq "preferred" -and $McpCommand -eq "npx -y binary-ninja-mcp --host custom.example --port 9014") "all overrides generate wizard command"
    $Script:Fixture = "alias connected`nnpx -y binary-ninja-mcp --host alias --port 9009`npreferred disconnected`nnpx -y binary-ninja-mcp --host custom.example --port 9014"
    Check-OpencodeMcp
    Assert ($McpFoundName -eq "preferred" -and $McpStatus -eq "disconnected") "configured name preferred over earlier backend match"

    function Set-Clipboard { param($Value) $Script:Clipboard = $Value }
    function Read-Host { return "y" }
    $Script:Calls = @()
    $wizard = (Set-OpencodeMcp 6>&1 | Out-String)
    Assert ($Clipboard -eq $McpCommand -and $wizard -match 'Type:    Local' -and $wizard -match 'Name:    preferred' -and $wizard -match 'one complete command') "wizard instructions and clipboard use generated command"
    Assert (($Calls -join ',') -eq "mcp add,mcp list") "interactive wizard followed by captured verification"
    function Set-Clipboard { throw "Clipboard unavailable" }
    $Script:Calls = @()
    $wizard = (Set-OpencodeMcp 6>&1 | Out-String)
    Assert ($wizard -match 'Could not copy to the clipboard' -and ($Calls -join ',') -eq "mcp add,mcp list") "clipboard failure does not block configuration"
    Assert ((Invoke-OpencodeCapture -Arguments @("--version")) -eq "1.2.3") "version prefers executable"

    # A real child process emits native stderr through a shim-like function.
    Remove-Item Function:opencode.exe
    $Script:PowerShellExe = (Get-Process -Id $PID).Path
    function opencode {
        & $Script:PowerShellExe -NoProfile -Command '[Console]::Out.WriteLine("native stdout"); [Console]::Error.WriteLine("native stderr"); exit 1'
    }
    $captured = Invoke-OpencodeCapture -Arguments @("mcp", "list")
    Assert ($captured -match 'native stdout' -and $captured -match 'native stderr' -and $captured -notmatch 'NativeCommandError|CategoryInfo|FullyQualifiedErrorId') "shim native stderr captured as plain text without error formatting"
    $captured = Invoke-OpencodeCapture -Arguments @("--version")
    Assert ($captured -match 'native stderr' -and $captured -notmatch 'NativeCommandError|CategoryInfo|FullyQualifiedErrorId') "shim version stderr captured safely"
    Assert ($ErrorActionPreference -eq "Stop") "capture restores error preference"
    Write-Host "All PowerShell MCP setup tests passed."
} finally {
    foreach ($name in $savedEnv.Keys) {
        [Environment]::SetEnvironmentVariable($name, $savedEnv[$name])
    }
}
