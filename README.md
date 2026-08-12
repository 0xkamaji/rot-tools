# RotBot
```text
   .-.
  [x_o]
  /|%|\
   / \
  ROTBOT
```

RotBot is a personal command-line wrapper for Git, AI coding agents, durable context, and SignalRot management.

It provides short commands for common workflows while keeping important actions visible and confirmable.

## Setup

### Requirements

* Python 3.11+
* Git
* Codex or OpenCode for AI commands
* `rsync` for SignalRot publishing

### Install

```bash
git clone git@github.com:0xkamaji/rotbot.git
cd rotbot
./setup.sh
```

Confirm the installation:

```bash
rot --help
```

RotBot installs two equivalent commands:

```bash
rot
rotbot
```

It can also run directly from the repository:

```bash
python -m rotbot --help
```

## Interactive Rot

Run `rot` without arguments to start a persistent, deterministic Rot session:

```bash
rot
```

```text
rot> git status
rot> context inspect
rot> cd ~/dev/signalrot
rot> status
rot> ask "What should I work on next?"
rot> pwd
rot> exit
```

The session keeps its working directory between commands and refreshes the
resolved project after `cd`. Enter `help` for the concise interactive command
list, `clear` to redraw the header, or `exit`/`quit` to leave.

On supported interactive terminals, Rot reserves the top rows for a fixed
session header while command output scrolls beneath it. Redirected output and
terminals without ANSI support use a plain one-time header instead. Rot restores
the terminal's normal scroll region when the session exits.

Every normal Rot command can be entered without the leading `rot` while inside
the session. The REPL uses the same parser and command handlers as the one-shot
CLI, including aliases, options, help, and explicit AI commands:

```text
rot> ask "What should I work on next?"
rot> wtf --deep rotbot/contexts
rot> git status --fetch
```

Unknown text is not interpreted as an implicit shell command or AI request.

### Interactive command history

Interactive Rot uses the terminal's standard line editor when available. Use
Up/Down to recall commands, Left/Right to edit, Ctrl+A/Ctrl+E to move across the
line, and Ctrl+R to search previous commands.

```text
rot> git status
rot> context inspect
rot> history
rot> history 10
```

Submitted `history` and exit commands are retained like other completed input.

History is bounded to the most recent 5000 commands and persists locally in
`~/.config/rot/history` (or the corresponding `XDG_CONFIG_HOME` location). The
file is private local UI state, created with user-only permissions where the
platform supports them. It is not portable RotBot context and is never included
in prompts sent to Codex, OpenCode, or another AI backend.

## Git commands

| Command          | Alias      | Purpose                                              |
| ---------------- | ---------- | ---------------------------------------------------- |
| `rot git status` | —          | Show branch, changes, remote sync, and latest commit |
| `rot git pull`   | `rot pull` | Pull changes from the configured upstream            |
| `rot git push`   | `rot push` | Commit and push local changes                        |

### Git status

```bash
rot git status
```

| Flag      | Purpose                                                         |
| --------- | --------------------------------------------------------------- |
| `--fetch` | Fetch before checking whether the repository is ahead or behind |

Without `--fetch`, RotBot compares against the locally cached remote branch.

```bash
rot git status --fetch
```

### Git push

```bash
rot push
rot push -m "Add Git status command"
```

| Flag              | Purpose                                          |
| ----------------- | ------------------------------------------------ |
| `-m`, `--message` | Supply the commit message                        |
| `--review`        | Ask an AI agent to review changes before pushing |
| `--agent AGENT`   | Choose `codex` or `opencode`                     |
| `--note TEXT`     | Add instructions for the review                  |

Example:

```bash
rot push --review --agent codex \
  --note "Focus on CLI compatibility"
```

The same options work with:

```bash
rot git push
```

## Understanding code with `wtf`

`rot wtf` collects relevant project evidence and asks an AI agent to explain it.

```bash
rot wtf
rot wtf path/to/file.py
rot wtf path/to/directory
```

| Flag            | Purpose                         |
| --------------- | ------------------------------- |
| `--deep`        | Inspect a directory more deeply |
| `--note TEXT`   | Tell the agent what to focus on |
| `--agent AGENT` | Choose `codex` or `opencode`    |

Examples:

```bash
rot wtf rotbot/contexts/matching.py
```

```bash
rot wtf --deep rotbot/contexts
```

```bash
rot wtf --note "Explain how this command reaches its handler"
```

## Asking an agent

Use `rot ask` to send a context-aware request to a supported coding agent:

```bash
rot ask "Explain how context matching works"
```

| Flag            | Purpose                      |
| --------------- | ---------------------------- |
| `--agent AGENT` | Choose `codex` or `opencode` |

Example:

```bash
rot ask "Review the current CLI structure" --agent codex
```

Set a default agent with:

```bash
export ROTBOT_AGENT=opencode
```

If no agent is selected, RotBot uses the first supported agent it finds.

`rot ask` resolves the current assistant, user, machine, project, and working
directory, then sends their portable RotBot context with the request. Project
identity and current state are included when a project resolves; project vision
is not included automatically.

Rot is the persistent assistant identity. Codex and OpenCode are execution
backends operating through that identity, not replacements for it.

`rot ask` uses portable RotBot context. Local/private context may help RotBot
resolve the current environment but is not included in prompts sent to AI
backends. In particular, prompt construction loads only the portable machine
files under `context/machines/`, never installation-specific local machine
records.

## Contexts

Contexts give RotBot durable knowledge about projects, people, and machines.

A context generally contains:

```text
context/
├── projects/
│   └── NAME/
│       ├── metadata.toml
│       ├── identity.md
│       ├── state.md
│       ├── vision.md
│       └── match.md
├── machines/
│   └── NAME/
│       ├── metadata.toml
│       ├── identity.md
│       └── software.toml
└── people/
    ├── contact/
    │   └── NAME/
    ├── user/
    │   └── NAME/
    └── assistant/
        └── NAME/
            ├── metadata.toml
            ├── identity.md
            ├── preferences.md
            ├── relationship.md
            ├── state.md
            ├── experience.md (user role only)
            └── priorities.md (user role only)
```

Project contexts are still addressed by name, such as `rotbot` or `signalrot`;
the `projects/` filesystem category is not part of the public context name.
Person contexts are grouped under `people/contact/`, `people/user/`, or
`people/assistant/`. Their names remain unique across all three roles, and they
are created through the same interactive add command as project contexts.
Machine contexts keep safe identity and normalized hardware facts under
`machines/NAME/`. Private host-specific facts use the same machine name in one
TOML file under RotBot's platform-aware local configuration directory, such as
`~/.config/rot/machines/NAME.toml`.

During machine creation, choose whether to inspect the current system or leave
the context empty for manual editing. Inspection is deterministic and local;
detected portable and private facts are shown separately and require separate
approval. Private facts default to declined. Local records may describe
hostnames, addresses, network interfaces, users, and SSH availability, but must
never contain passwords, private keys, tokens, cookies, recovery codes, or
other authentication secrets. RotBot never automatically loads local records
when listing, showing, matching, or building AI prompts.

Every active project, person, and machine context has a portable UUID in its
`metadata.toml`. Names remain the human-facing CLI identifiers; local bindings
store UUIDs so renaming a context does not change its backend identity.

Project files:

| File          | Purpose                                       |
| ------------- | --------------------------------------------- |
| `metadata.toml` | Stable UUID and display/lookup name         |
| `identity.md` | What the project is and its stable principles |
| `state.md`    | What currently exists                         |
| `vision.md`   | Where the project is going                    |
| `match.md`    | Facts used to recognize the project           |

Machine files:

| File            | Purpose                                      |
| --------------- | -------------------------------------------- |
| `metadata.toml` | Portable identity and normalized hardware facts |
| `identity.md`   | Human-authored purpose and environment context |
| `software.toml` | Deliberately selected relevant software      |

### Context commands

Run `rot context` without a subcommand to choose an action from an interactive
menu. Direct subcommands remain available for faster scripted use.

| Command                      | Purpose                            |
| ---------------------------- | ---------------------------------- |
| `rot context list`           | List available contexts            |
| `rot context inspect`        | Inspect the active identities, machine, directory, and project |
| `rot context show [NAME]`    | Display the current session or a saved context |
| `rot context bind PATH`      | Detect and bind a local project    |
| `rot context bind NAME PATH` | Bind a specific context            |
| `rot context add`            | Interactively create a project, person, or machine context |
| `rot context add machine [NAME]` | Create a machine directly, then inspect or leave empty |
| `rot context add user [NAME]` | Create a user with the person workflow |
| `rot context add assistant [NAME]` | Create an assistant with the person workflow |
| `rot context mod [NAME]`     | Add categorized information to a person context |
| `rot context delete [NAME]` | Archive a context, or choose one from a list |

Archived contexts are moved beneath the hidden `context/.archive/` directory,
outside RotBot's active discovery paths. Each kind has its own bucket:
`projects/`, `machines/`, `contacts/`, `users/`, or `assistants/`. Archiving a
project also removes its local source and production bindings so the name can
be recreated cleanly. Archiving a portable machine context does not modify its
installation-specific local metadata file.

Rot stores this installation's active portable context IDs in
`~/.config/rot/config.toml`:

```toml
[user]
id = "497e5a65-9bcf-4ddb-90bc-d1d5535a8c63"

[assistant]
id = "37afbc72-8f56-4fed-90a3-eaead836e13e"

[machine]
id = "57eab66e-7041-440a-83d8-7ff3ab39ed11"
```

Inspect the context associated with the current working directory:

```bash
rot context inspect
```

On first use, inspection prompts for an existing or new user and assistant, then
inspects and registers the local machine if needed. These three selections are
persisted locally. Projects remain directory-specific and are never saved as a
global default. The final summary excludes local/private machine metadata.
Normal `rot ask` resolution is non-interactive and does not bootstrap missing
bindings.

Inspect the current host:

```bash
rot machine inspect
```

Machine inspection always displays current detected state first. It then reports
whether a configured or locally associated machine context already exists. Any
new registration or rebind requires explicit confirmation and stores the portable
context's UUID. New registration also stores detected private local facts in
`~/.config/rot/machines/` so later inspections can recognize the host. Declining
leaves files and configuration unchanged. Existing portable machine contexts are
never overwritten. Inspection never invokes AI, scans installed packages, or
inspects a remote machine.

`rot context mod` currently supports people. It reads the selected Markdown
file's existing `##` headings, adds information beneath one of them, or creates
a new heading with a reusable guidance comment.

When showing a person, RotBot displays only populated Markdown sections. Empty
template headings, guidance comments, metadata, and role-inapplicable files are
omitted.

### Show a context

Run without a name to choose between the current invocation context and a saved
project, person, or machine context:

```bash
rot context show
```

The current-session option is read-only and does not bootstrap missing local
bindings. It shows the same resolved identities, machine, directory, project,
identification sources, and warnings used by context inspection.

```bash
rot context show rotbot
rot context show signalrot
```

| Flag       | Purpose                                |
| ---------- | -------------------------------------- |
| `--vision` | Show only a project's future direction |

```bash
rot context show rotbot --vision
```

### Bind a project

Bind the current directory by matching it against known contexts:

```bash
rot context bind .
```

Bind a specific context:

```bash
rot context bind signalrot ~/github/signalrot
```

| Option            | Purpose                                  |
| ----------------- | ---------------------------------------- |
| `--as source`     | Bind the path as the editable source     |
| `--as production` | Bind the path as the production location |

Example:

```bash
rot context bind signalrot ~/github/signalrot --as source
```

Local paths are saved in RotBot’s local configuration rather than portable context files.

## SignalRot wrapper

The `rot sr` namespace is a specialized wrapper for maintaining SignalRot.

It combines RotBot’s generic Git, context, AI, and confirmation systems with SignalRot-specific operations.

| Command          | Purpose                                           |
| ---------------- | ------------------------------------------------- |
| `rot sr status`  | Check whether SignalRot is responding             |
| `rot sr context` | Show the SignalRot context dashboard              |
| `rot sr diff`    | Preview differences between source and production |
| `rot sr pull`    | Pull SignalRot changes                            |
| `rot sr push`    | Push SignalRot changes                            |
| `rot sr publish` | Push changes and publish the site                 |

### SignalRot status

```bash
rot sr status
```

Reports the site’s HTTP status and response time.

### SignalRot context

```bash
rot sr context
```

| Flag        | Purpose                                          |
| ----------- | ------------------------------------------------ |
| `--refresh` | Inspect SignalRot and refresh its recorded state |
| `--full`    | Show the complete identity and state context     |

```bash
rot sr context --refresh
```

```bash
rot sr context --full
```

This differs from:

```bash
rot context show signalrot
```

`rot context show signalrot` displays the portable context files.
`rot sr context` provides a SignalRot-specific dashboard and refresh workflow.
`rot sr context --full` is a shortcut for the complete portable context display.

### Compare source and production

```bash
rot sr diff
```

This performs a dry-run comparison and explains what publishing would add, replace, or remove.

### Publish SignalRot

```bash
rot sr publish
```

RotBot previews and confirms the Git and deployment operations before changing production.

| Flag            | Purpose                                           |
| --------------- | ------------------------------------------------- |
| `--review`      | Review changes with an AI agent before publishing |
| `--agent AGENT` | Choose `codex` or `opencode`                      |
| `--note TEXT`   | Add review instructions                           |

Example:

```bash
rot sr publish --review --agent codex \
  --note "Check for broken links and deployment risks"
```

SignalRot locations can be configured through context bindings or environment variables:

```bash
export SIGNALROT_REPO=/path/to/signalrot
export SIGNALROT_WEB_ROOT=/path/to/live/site
```

Environment variables take precedence over saved bindings.

## Help

Use `--help` at any level to see a summary of the commands and options supported
by the installed version:

```bash
rot --help
rot git --help
rot git push --help
rot wtf --help
rot context --help
rot sr --help
rot sr publish --help
```

Use verbose help for a man-page-style view of the current command and every
subcommand below it, including positional fields, flags, choices, and examples:

```bash
rot -hv
rot --help-verbose
rot git -hv
```

`rot -hv` prints the complete command tree. A scoped form such as
`rot git -hv` prints only that command group and its subcommands.
