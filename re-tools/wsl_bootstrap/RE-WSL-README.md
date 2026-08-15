# RE WSL Helper

Run:

```bash
chmod +x re-wsl.sh
./re-wsl.sh
```

## Menu

### 1. Install / bootstrap RE environment

Installs:

- curl / CA certificates
- Python 3
- Git
- NVM
- Node.js LTS, npm, and npx
- OpenCode

It also sets WSL mirrored networking so Windows and WSL can use `localhost` for the Binary Ninja MCP and debugger connections.

The script **does not write OpenCode's MCP configuration**. Instead, it launches OpenCode's own supported wizard:

```bash
opencode mcp add
```

Choose:

- Type: `Local`
- Name: `binary-ninja`
- Command:

```text
npx -y binary-ninja-mcp --host localhost --port 9009
```

If `.wslconfig` changed, run this once from Windows PowerShell:

```powershell
wsl --shutdown
```

Then reopen WSL.

### 2. Debug server

Choose menu option 2 to launch the persistent debugger-server menu:

```text
re-tools/wsl_bootstrap/debug-server.sh
```

The debugger server is a state-driven, persistent workflow: it starts a server once, shows its status on every run, and only stops processes it can positively identify as its own from its state files.

#### Linux server

```text
lldb-server
persistent server mode
default bind: 127.0.0.1
default port: 31337
```

Use "Setup / repair debugger tools" to install `lldb-server` (via `pacman` or `apt-get`) if it is missing. Binary Ninja's managed debugger package is preferred when present at:

```text
~/.local/share/rot-tools/debuggers/binary-ninja/linux/plugins/lldb/lldb-server
```

The resolution order is: explicit `LLDB_SERVER`, then the managed Binary Ninja package, then legacy managed layouts, then `lldb-server` from `PATH`.

In Binary Ninja:

- Debugger → Connect to Remote Process
- Adapter: `LLDB`
- Address: `localhost`
- Port: `31337`

The Linux server is persistent; it stays running between sessions and is stopped from this menu (`Stop server` / `Stop all Rot debug servers`).

#### Windows server

```text
dbgsrv.exe
invoked through PowerShell / WSL interop when available
default bind: 127.0.0.1
default port: 31338
```

`debug-server.sh` delegates Windows management to `debug-server.ps1` through `powershell.exe` / `wslpath` when WSL interop is available.

You can also run the PowerShell script directly from Windows, without WSL:

```powershell
.\debug-server.ps1
```

Typical direct use:

```powershell
.\debug-server.ps1 -Action Status
.\debug-server.ps1 -Action Start -Port 31338
.\debug-server.ps1 -Action Stop
```

`dbgsrv.exe` is located via `DBGSRV_PATH`, `BN_DEBUGGER_WIN32`, a local `debugger-win32` package next to the script, or the Windows SDK Debugging Tools installation.

The Windows side records the PID, the resolved executable path, and the process start time in per-user state under `%LOCALAPPDATA%\rot-tools\debug-server`. A process is only stopped when all recorded identity fields still match; stale or unverifiable PIDs are never killed.

#### Stop safety

"Stop all Rot debug servers" only stops servers positively identified from Rot-owned state files. It never runs `pkill`/`killall lldb-server`/`taskkill /IM dbgsrv.exe`. Unverifiable processes are refused and reported.

## Binary Ninja MCP

In Binary Ninja, start the MCP server from the Plugins menu.

Test from WSL:

```bash
curl http://localhost:9009/binaries
opencode mcp list
```

---

> TODO: the debugger tooling currently lives under `re-tools/wsl_bootstrap/` for historical reasons, but `debug-server.sh` now supports native Linux and Windows interop and `debug-server.ps1` runs directly on Windows. It may move into a more general `re-tools/debug/` location in a future pass.
