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
- gdbserver
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

## 2. Start gdbserver

Choose menu option 2 and enter the Linux executable you want to debug.

The helper starts:

```text
127.0.0.1:31337
```

In Binary Ninja:

- Debugger → Connect to Remote Process
- Adapter: `GDB RSP`
- Address: `localhost`
- Port: `31337`

`gdbserver` is one-shot. When the program exits, run option 2 again for another debug session.

## Binary Ninja MCP

In Binary Ninja, start the MCP server from the Plugins menu.

Test from WSL:

```bash
curl http://localhost:9009/binaries
opencode mcp list
```
