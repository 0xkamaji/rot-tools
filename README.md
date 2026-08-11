# RotBot

```text
   .-.
  [x_o]
  /|%|\
   / \
  ROTBOT
```

RotBot is a small, local Python command-line helper for Git repository work,
deterministic project contexts, AI-assisted inspection, and personal SignalRot
workflows. It favors simple, inspectable implementations and keeps portable
context documents separate from machine-local path bindings.

Both command names are supported:

```bash
rot --help
rotbot --help
```

## Highlights

- Concise, read-only Git repository status summaries
- Pull and push workflows available as short commands and under `rot git`
- Optional OpenCode or Codex reviews before committing changes
- Deterministic context discovery through portable `match.md` files
- Separate identity, current-state, and optional vision documents
- Local source and production bindings stored outside the repository
- SignalRot-specific status, review, comparison, and publishing workflows
- No framework or large runtime dependency tree

## Requirements

- Python 3.11 or newer
- Git
- A POSIX-style shell for `setup.sh`
- OpenCode or Codex only when using AI-assisted commands
- `rsync` and appropriate local permissions for SignalRot deployment commands

## Installation

Clone the repository and run the local setup script:

```bash
git clone git@github.com:0xkamaji/rotbot.git
cd rotbot
./setup.sh
```

The setup script installs `rot` and `rotbot` into `~/.local/bin` and can add
that directory to Bash, Zsh, or Fish configuration.

RotBot can also run directly from the repository:

```bash
python -m rotbot --help
```

## Commands

### Git

The original short commands remain available:

```bash
rot pull
rot push
rot push --review
```

The same workflows are grouped under `rot git`:

```bash
rot git pull
rot git push
rot git push --review
rot git status
rot git status --fetch
```

`rot git status` is local and read-only. It summarizes the branch, upstream,
working tree, cached ahead/behind state, and latest commit without contacting a
remote. Use `--fetch` to refresh the configured upstream remote before comparing.

Push options include:

```text
--review                 Ask an agent to review changes before committing
-m, --message MESSAGE    Supply the commit message directly
-a, --agent AGENT        Select opencode or codex
-n, --note NOTE          Add a request or caveat to the review
```

### Ask And Inspect

```bash
rot ask "Explain this error"
rot ask "Review this approach" --agent codex

rot wtf
rot wtf path/to/file.py
rot wtf --deep path/to/project
rot wtf --note "focus on deployment risks"
```

`rot wtf` assembles bounded local evidence and asks the configured agent to
explain a file or directory. It does not replace normal code review or testing.

### Contexts

```bash
rot context list
rot context show signalrot
rot context show rotbot --vision
rot context bind .
rot context bind signalrot ~/github/signalrot --as source
rot context add another-project /path/to/project
```

A portable context directory has this shape:

```text
context/NAME/
├── identity.md    # stable, human-maintained identity
├── state.md       # current observed state
├── vision.md      # optional, speculative and nonbinding
└── match.md       # optional deterministic recognition facts
```

`identity.md` and `state.md` are the standard AI context. Vision remains
separate unless explicitly requested, while `match.md` and machine-local paths
never enter normal context prompts.

`rot context bind` recognizes a source checkout or production directory using
verified Git remotes, required paths, and supported local Caddy configuration.
Bindings are confirmed before they are saved.

`rot context add` drafts identity and state through the selected coding agent,
generates `match.md` deterministically, previews the complete proposal, and
registers the source path only after confirmation. It never creates `vision.md`.

## SignalRot

SignalRot remains a separate, opinionated integration:

```bash
rot sr status
rot sr context
rot sr context --refresh
rot sr diff
rot sr pull
rot sr push
rot sr publish
```

The distinction is intentional:

```text
rot context show signalrot   Generic portable context display
rot sr context               SignalRot dashboard and state refresh workflow
```

SignalRot source and production paths can be supplied through context bindings
or environment overrides:

```bash
export SIGNALROT_REPO=/path/to/signalrot
export SIGNALROT_WEB_ROOT=/path/to/live/site
```

Environment values take precedence over local bindings.

## Agent Selection

RotBot supports OpenCode and Codex. Select an agent per command:

```bash
rot ask "Summarize this repository" --agent opencode
rot git push --review --agent codex
```

Or set a default:

```bash
export ROTBOT_AGENT=opencode
```

Without an explicit selection, RotBot uses the first supported agent available
on the local system.

## Local Configuration

Confirmed path bindings are stored outside the repository:

```text
${XDG_CONFIG_HOME:-~/.config}/rotbot/config.toml
```

Example:

```toml
[contexts.signalrot]
source_path = "/home/user/github/signalrot"
production_path = "/var/www/signalrot"

[contexts.rotbot]
source_path = "/home/user/github/rotbot"
```

RotBot preserves unrelated TOML configuration and updates binding files through
same-directory atomic replacement.

## Project Layout

```text
rotbot/
├── agents/                    Agent selection and execution
├── cli/                       Argument parsing and dispatch
├── commands/                  General Git and inspection commands
├── contexts/                  Loading, matching, binding, and configuration
├── integrations/signalrot/    SignalRot-specific workflows
└── ui/                        Terminal presentation

context/                       Portable Markdown context data
tests/                         Standard-library unittest suite
```

## Development

Run the complete test suite:

```bash
python -m unittest discover -s tests -v
```

Compile-check the package and tests:

```bash
python -m compileall -q rotbot tests rotbot.py
```

Tests use temporary repositories, local bare remotes, and temporary XDG
configuration homes. They do not require the developer's real RotBot
configuration or SignalRot deployment.

## Safety

RotBot keeps ordinary status, matching, loading, and display operations
read-only. Commands that can change repositories, context files, local bindings,
or deployments show their intent and require confirmation where applicable.
Review command output before approving any mutation.
