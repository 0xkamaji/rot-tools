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
kamaji ❯ git status
kamaji ❯ context inspect
kamaji ❯ cd ~/dev/signalrot
kamaji ❯ status
kamaji ❯ ask "What should I work on next?"
kamaji ❯ pwd
kamaji ❯ exit
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
kamaji ❯ ask "What should I work on next?"
kamaji ❯ wtf --deep rotbot/contexts
kamaji ❯ git status --fetch
```

Interactive Rot also recognizes installed shell commands and runs them locally
in the session's current directory, with normal pipes, redirects, environment,
stdin, stdout, and stderr:

```text
kamaji ❯ ls -lah
kamaji ❯ rg "CommandHistory" rotbot/ | head -20
kamaji ❯ python --version
```

Input is routed deterministically. Session built-ins run first, exact Rot
commands use Rot's shared CLI parser, malformed Rot namespaces remain parser
errors, and recognized executables with shell-shaped arguments run through the
user's shell. English-like executable names such as `find` use simple flags,
paths, file arguments, assignments, and shell operators to distinguish command
shape from prose. Remaining natural language continues one OpenCode conversation
for the lifetime of the current Rot session:

```text
kamaji ❯ why is the project resolver designed this way?
kamaji ❯ yeah, but what happens after cd?
```

Use `? MESSAGE` to force conversation when a word is also an executable, and
`! COMMAND` to force shell execution when a command overlaps Rot syntax:

```text
kamaji ❯ ? find a clearer way to explain project matching
kamaji ❯ !git status --short
```

Strong first-token typos are compared against Rot commands and executable names
discovered from the current `PATH`. Rot only suggests a correction; it never
executes the corrected command automatically. Executable discovery is cached by
the `PATH` value and refreshes naturally after `PATH` changes.

OpenCode starts lazily on the first conversational message. Interactive Rot
uses OpenCode's official CLI session support and reuses the returned session ID
for later conversational turns. After `cd` or a successful context-changing Rot
command, the next AI turn refreshes shareable Rot context in that same session.

While a conversational request is waiting for visible output, interactive Rot
shows an animated `rot · thinking` status. User-visible OpenCode text events are
rendered as soon as the backend emits them; reasoning, tool, protocol, and debug
events remain hidden. OpenCode's CLI currently emits completed text parts rather
than token deltas, so streaming occurs at those genuine backend event
boundaries. Rot never simulates streaming by slowly replaying a completed
answer. Backends without native streaming fall back to one completed response.

Rot commands, shell commands, their output, and terminal command history are
not automatically sent to AI. Natural-language fallback is configured without
shell or edit permission; it is conversation, not authorization to execute.

Interactive prompts use the resolved user identity, such as `kamaji ❯`. AI
answers use the resolved assistant identity as a quiet speaker heading:

```text
kamaji ❯ why is the resolver structured this way?

rot [x_o]
The resolver separates portable context from local resolution data because...
```

Shell and deterministic Rot output remain raw and unlabeled. The larger
Question/Response framing remains limited to one-shot `rot ask` output.

### Talk and work authority

Every new interactive Rot session starts in `TALK`, and WORK authority is never
restored across process restarts.

```text
kamaji ❯ work

rot [x_o]
Work mode enabled for rotbot.

kamaji ❯ talk

rot [x_o]
Talk mode enabled.
```

`TALK` is technically enforced with a Rot-owned OpenCode primary agent whose
tool permissions deny everything. Rot selects that agent explicitly, runs
OpenCode without external plugins, and also supplies a deny-all runtime
permission map as defense in depth.
Conversational AI can reason and respond, but cannot use shell, filesystem,
subagent, web, or mutation tools. Explicit commands typed by the user still run
normally.

`WORK` is an explicit temporary elevation. It requires a resolved active
project, permits the configured agentic backend inside that workspace, and
denies external-directory access. Changing to a different resolved project
automatically returns Rot to TALK. Changing directories within the same project
does not revoke WORK. Natural language and `? MESSAGE` never elevate authority.

The banner displays authority and conversation state independently:

```text
TALK · AI: idle
WORK · AI: idle
TALK · AI: active
WORK · AI: active
```

`AI: idle` means no request is active and no conversation has started.
`AI: thinking` means a request is waiting for visible backend output.
`AI: active` means a conversation exists and is not currently waiting.
Switching TALK/WORK does not itself start AI or erase conversation history.

### Conversation ownership

Rot owns the interactive AI conversation identity, canonical user/assistant
transcript, context provenance, and lifecycle. The first conversational turn
creates a private local record under:

```text
$XDG_DATA_HOME/rotbot/conversations/rotconv_<id>/
    metadata.toml
    transcript.jsonl
```

Without `XDG_DATA_HOME`, the default root is
`~/.local/share/rotbot/conversations/`. Directories use mode `0700` and files
use mode `0600` on Unix-like systems. User messages are appended before calling
the backend, assistant messages are appended after successful inference, and
failed or interrupted user turns remain recorded. Graceful exit marks the
conversation closed but does not delete it.

Inspect the local records with:

```bash
rot ai sessions
rot ai session show
rot ai session show rotconv_<id>
```

Omit the ID to choose a conversation from a numbered menu.

OpenCode is an execution backend. Its session ID is recorded as backend-state
metadata beneath the Rot conversation; it is not the Rot conversation ID and is
not the only copy of what was said. OpenCode may reuse its session as an
efficient working cache, while Rot remains authoritative for the transcript.

The installed OpenCode version stores sessions and transcripts persistently in
its local SQLite database under `~/.local/share/opencode/opencode.db`. OpenCode
exports also expose model-provider metadata. Provider-side inference state and
operational retention are governed by provider APIs and policies; Rot does not
currently inspect or delete those records. No local or cloud conversation purge
command is implemented yet.

Raw Rot conversations are currently retained locally indefinitely. There is no
automatic summarization, Qwen processing, memory extraction, semantic-context
promotion, expiration, synchronization, or provider deletion. Saving the local
transcript does not send additional information to a provider; cloud exposure
is still determined only by the existing AI routing and context compiler.

These remain separate systems:

```text
CommandHistory   terminal recall and local input history
AIConversation   user/assistant conversational transcript
Rot context      curated user, assistant, machine, and project knowledge
```

Command history records what was typed for terminal recall. AI conversation
history records only conversational user/Rot turns. Persistent context records
what Rot durably knows in the private local data store. Shell commands, shell
output, deterministic Rot commands, and command history are not copied into AI
conversation transcripts. Conversation storage is not semantic context and is
not included by `rot push` or automatically reused as cloud context.

### Interactive command history

Interactive Rot uses the terminal's standard line editor when available. Use
Up/Down to recall commands, Left/Right to edit, Ctrl+A/Ctrl+E to move across the
line, Ctrl+R to search previous commands, and Tab to complete Rot commands,
available executables, context names, and filesystem paths.

```text
kamaji ❯ context <Tab>
add  bind  delete  inspect  list  mod  show

kamaji ❯ cd ~/Doc<Tab>
kamaji ❯ cd ~/Documents/
```

Rot command, subcommand, option, and fixed-choice completion comes from the
same argparse grammar used for execution. Executables come from the current
`PATH`, and paths resolve against the live RotSession directory. Arbitrary
third-party subcommand grammars and shell-native completion engines are not yet
included. Platforms without a compatible readline backend continue without
Tab completion; Windows PATHEXT and shell-specific built-ins remain an isolated
future completion-provider concern rather than Unix assumptions in the REPL.

```text
kamaji ❯ git status
kamaji ❯ context inspect
kamaji ❯ history
kamaji ❯ history 10
```

Submitted `history` and exit commands are retained like other completed input.

History is bounded to the most recent 5000 commands and persists locally in
`~/.config/rotbot/history` (or the corresponding `XDG_CONFIG_HOME` location). The
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

`rot ask` resolves the current assistant, user, machine, project, and runtime
capability state, then compiles the allowlisted external context described
below. Project vision is not included automatically.

Rot is the persistent assistant identity. Codex and OpenCode are execution
backends operating through that identity, not replacements for it.

`rot ask` and interactive AI use the same deterministic egress gate. Only safe
entity names, permitted runtime capability state, and semantic files under
`shareable/` are eligible. UUIDs, local paths, `local/` semantics, and machine
configuration are excluded by construction.

## Contexts

Contexts give RotBot durable information about users, assistants, contacts,
machines, and projects. Runtime context is private local data, not repository
source. It lives under `$XDG_DATA_HOME/rotbot/contexts/`, with fallback to
`~/.local/share/rotbot/contexts/`:

```text
contexts/
├── users/NAME/{metadata.toml,local/,shareable/}
├── assistants/NAME/{metadata.toml,capabilities.toml,local/,shareable/}
├── contacts/NAME/{metadata.toml,local/,shareable/}
├── machines/NAME/{metadata.toml,local/,shareable/}
└── projects/NAME/{metadata.toml,local/,shareable/}
```

Both `local/` and `shareable/` remain private local Rot data and stay outside
Git. `shareable` does not mean public, publishable, or safe for source control.
It means only that a semantic file is eligible for an explicitly permitted
external AI request. Local Rot reasoning may load the union of both namespaces.
External AI context starts empty and allowlists `shareable/` only. Unclassified
legacy data migrates to `local/` by default.

Stable UUIDs in `metadata.toml` remain entity identity; paths and display names
are not identity. Safe outbound entity envelopes expose only type/name labels,
not UUIDs, private paths, network identifiers, or local configuration.

Machine-specific paths, identity bindings, project bindings, routing, and host
facts remain under `$XDG_CONFIG_HOME/rotbot/`, normally `~/.config/rotbot/`.
Configuration is not semantic context and cannot enter the cloud egress
pipeline. The repository contains only code, schemas/templates, tests, and the
built-in Rot definition at `builtin/assistants/rot/`.

During machine creation, choose whether to inspect the current system or leave
the context empty for manual editing. Inspection is deterministic and local;
detected portable and private facts are shown separately and require separate
approval. Private facts default to declined. Local records may describe
hostnames, addresses, network interfaces, users, and SSH availability, but must
never contain passwords, private keys, tokens, cookies, recovery codes, or
other authentication secrets. RotBot never automatically loads local records
when listing, showing, matching, or building AI prompts.

Every active project, user, assistant, contact, and machine context has a portable UUID in its
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

Assistant `capabilities.toml` declares operating intent, such as a safe TALK
default and whether project-scoped WORK may be requested. It does not grant
permissions directly. RotBot core intersects that policy with supported modes,
the current project scope, and backend enforcement to produce a runtime
capability state.

The architecture boundary is:

```text
Context       durable entity knowledge and intended policy
RotBot core   mechanisms, validation, and enforcement
RotSession    current TALK/WORK mode, cwd, project scope, and AI activity
```

Context says what is intended or known. Core code enforces what that means.
Runtime state says what is true right now. TALK/WORK transitions never write to
assistant context files.

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
| `rot context add`            | Interactively create a project, user, assistant, contact, or machine context |
| `rot context add user [NAME]` | Create a first-class user context |
| `rot context add assistant [NAME]` | Create an assistant with safe capability defaults |
| `rot context add machine [NAME]` | Create a machine directly, then inspect or leave empty |
| `rot context add user [NAME]` | Create a user with the person workflow |
| `rot context add assistant [NAME]` | Create an assistant with the person workflow |
| `rot context mod [NAME]`     | Add categorized information to a person context |
| `rot context delete [NAME]` | Archive a context, or choose one from a list |

Archived contexts are moved beneath the hidden `contexts/.archive/` data directory,
outside RotBot's active discovery paths. Each kind has its own bucket:
`projects/`, `machines/`, `contacts/`, `users/`, or `assistants/`. Archiving a
project also removes its local source and production bindings so the name can
be recreated cleanly. Archiving a portable machine context does not modify its
installation-specific local metadata file.

Rot stores this installation's active context IDs in
`~/.config/rotbot/config.toml`:

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
new registration or rebind requires explicit confirmation and stores the
context's UUID. New registration also stores detected private local facts in
`~/.config/rotbot/machines/` so later inspections can recognize the host. Declining
leaves files and configuration unchanged. Existing machine contexts are
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
