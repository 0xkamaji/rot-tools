import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
from typing import Iterator

from rotbot.contexts import accounts, entities
from rotbot.contexts.config import ConfigError, get_local_context_bindings
from rotbot.ui.terminal import rot_say


PUSH_CANCELLED = object()


def _capture_git(*args, working_directory=None):
    return subprocess.run(
        ["git", *args],
        capture_output=True,
        text=True,
        check=False,
        cwd=working_directory
    )


def _capture_git_bytes(*args, working_directory=None):
    return subprocess.run(
        ["git", *args],
        capture_output=True,
        check=False,
        cwd=working_directory
    )


class GitStatusError(Exception):
    pass


def _decode_ascii(value, description):
    try:
        return value.decode("ascii")
    except UnicodeDecodeError:
        raise GitStatusError(f"Git returned malformed {description} data.") from None


def _parse_porcelain_v2(output):
    parsed = {
        "oid": None,
        "branch": None,
        "detached": False,
        "upstream": None,
        "changes": []
    }
    records = output.split(b"\0")
    index = 0
    while index < len(records):
        record = records[index]
        index += 1
        if not record:
            continue
        if record.startswith(b"# branch.oid "):
            value = record[len(b"# branch.oid "):]
            parsed["oid"] = None if value == b"(initial)" else _decode_ascii(value, "object ID")
            continue
        if record.startswith(b"# branch.head "):
            value = record[len(b"# branch.head "):]
            if value == b"(detached)":
                parsed["detached"] = True
            else:
                parsed["branch"] = value.decode("utf-8", "surrogateescape")
            continue
        if record.startswith(b"# branch.upstream "):
            parsed["upstream"] = record[len(b"# branch.upstream "):].decode(
                "utf-8",
                "surrogateescape"
            )
            continue
        if record.startswith(b"# "):
            continue

        kind = record[:1]
        if kind == b"1":
            fields = record.split(b" ", 8)
            if len(fields) != 9 or len(fields[1]) != 2:
                raise GitStatusError("Git returned malformed ordinary status data.")
            parsed["changes"].append(("1", _decode_ascii(fields[1], "ordinary status")))
        elif kind == b"2":
            fields = record.split(b" ", 9)
            if (
                len(fields) != 10
                or len(fields[1]) != 2
                or index >= len(records)
                or not records[index]
            ):
                raise GitStatusError("Git returned malformed rename status data.")
            index += 1
            parsed["changes"].append(("2", _decode_ascii(fields[1], "rename status")))
        elif kind == b"u":
            fields = record.split(b" ", 10)
            if len(fields) != 11 or len(fields[1]) != 2:
                raise GitStatusError("Git returned malformed conflict status data.")
            parsed["changes"].append(("u", _decode_ascii(fields[1], "conflict status")))
        elif kind == b"?" and record.startswith(b"? ") and len(record) > 2:
            parsed["changes"].append(("?", "??"))
        elif kind != b"!":
            raise GitStatusError("Git returned an unsupported status record.")
    return parsed


def _worktree_summary(changes):
    counts = {
        "staged": 0,
        "modified": 0,
        "deleted": 0,
        "renamed": 0,
        "conflicted": 0,
        "untracked": 0
    }
    for kind, xy in changes:
        index_status, worktree_status = xy
        conflicted = kind == "u" or "U" in xy or xy in {"AA", "DD"}
        if conflicted:
            counts["conflicted"] += 1
        elif kind == "?":
            counts["untracked"] += 1
        else:
            if index_status != ".":
                counts["staged"] += 1
            if worktree_status in {"M", "T"}:
                counts["modified"] += 1
            if "D" in xy:
                counts["deleted"] += 1
            if kind == "2" or "R" in xy or "C" in xy:
                counts["renamed"] += 1

    total = len(changes)
    if not total:
        return "Clean", counts
    labels = []
    for key in ("staged", "modified", "deleted", "renamed", "conflicted", "untracked"):
        count = counts[key]
        if count:
            labels.append(f"{count} {key}")
    noun = "change" if total == 1 else "changed"
    return f"{total} {noun}\n            " + " · ".join(labels), counts


def _sync_state(repository, upstream, fetched):
    if not upstream:
        return "No upstream configured", None, None
    comparison = _capture_git(
        "rev-list",
        "--left-right",
        "--count",
        f"HEAD...{upstream}",
        working_directory=repository
    )
    if comparison.returncode != 0:
        return "Remote comparison unavailable", None, None
    fields = comparison.stdout.split()
    if len(fields) != 2 or not all(field.isdigit() for field in fields):
        raise GitStatusError("Git returned malformed ahead/behind data.")
    ahead, behind = (int(field) for field in fields)
    cached = "" if fetched else f" cached {upstream}"
    if ahead == 0 and behind == 0:
        message = "Up to date" if fetched else f"Up to date with cached {upstream}"
    elif ahead and not behind:
        message = (
            f"Ahead by {ahead} {_commit_word(ahead)}"
            if fetched
            else f"Ahead of{cached} by {ahead} {_commit_word(ahead)}"
        )
    elif behind and not ahead:
        message = (
            f"Behind by {behind} {_commit_word(behind)}"
            if fetched
            else f"Behind{cached} by {behind} {_commit_word(behind)}"
        )
    else:
        message = (
            f"Diverged: {ahead} ahead, {behind} behind"
            if fetched
            else f"Diverged from{cached}: {ahead} ahead, {behind} behind"
        )
    return message, ahead, behind


def _commit_word(count):
    return "commit" if count == 1 else "commits"


def _last_commit(repository):
    result = _capture_git(
        "log",
        "-1",
        "--format=%h%x00%cr%x00%s",
        working_directory=repository
    )
    if result.returncode != 0:
        detail = result.stderr.strip()
        raise GitStatusError("Could not inspect the last commit." + (f"\n{detail}" if detail else ""))
    fields = result.stdout.rstrip("\n").split("\0", 2)
    if len(fields) != 3:
        raise GitStatusError("Git returned malformed commit data.")
    return tuple(fields)


def _upstream_suggestion(repository, branch):
    if not branch:
        return "Configure an upstream branch"
    remotes = _capture_git("remote", working_directory=repository)
    names = [name for name in remotes.stdout.splitlines() if name] if remotes.returncode == 0 else []
    if len(names) == 1:
        return f"git push -u {names[0]} {branch}"
    return "Configure an upstream branch"


def _fetch_upstream(repository, branch):
    remote = _capture_git(
        "config",
        "--get",
        f"branch.{branch}.remote",
        working_directory=repository
    )
    remote_name = remote.stdout.strip() if remote.returncode == 0 else ""
    if not remote_name:
        raise GitStatusError("Could not determine the configured upstream remote.")
    fetched = _capture_git(
        "fetch",
        "--quiet",
        "--no-recurse-submodules",
        "--",
        remote_name,
        working_directory=repository
    )
    if fetched.returncode != 0:
        detail_lines = (fetched.stderr.strip() or fetched.stdout.strip()).splitlines()
        detail = "\n".join(detail_lines[-6:])
        raise GitStatusError(
            f"Could not fetch upstream remote '{remote_name}'."
            + (f"\n{detail}" if detail else "")
        )


def git_status(args):
    command_directory = os.getcwd()
    try:
        repository_result = _capture_git(
            "rev-parse",
            "--show-toplevel",
            working_directory=command_directory
        )
    except FileNotFoundError:
        rot_say("Git is not installed or is not available in PATH.")
        return 127
    except OSError as error:
        rot_say(f"Could not run Git.\n{error}")
        return 1
    if repository_result.returncode != 0:
        rot_say("The current directory is not inside a Git repository.")
        return 1
    repository = repository_result.stdout.strip()

    try:
        status_result = _capture_git_bytes(
            "--no-optional-locks",
            "status",
            "--porcelain=v2",
            "--branch",
            "--ahead-behind",
            "--find-renames",
            "--untracked-files=all",
            "-z",
            working_directory=repository
        )
        if status_result.returncode != 0:
            detail = status_result.stderr.decode("utf-8", "replace").strip()
            raise GitStatusError("Could not inspect Git status." + (f"\n{detail}" if detail else ""))
        status = _parse_porcelain_v2(status_result.stdout)

        fetched = False
        if getattr(args, "fetch", False) and status["branch"] and status["upstream"]:
            _fetch_upstream(repository, status["branch"])
            fetched = True
            status_result = _capture_git_bytes(
                "--no-optional-locks",
                "status",
                "--porcelain=v2",
                "--branch",
                "--ahead-behind",
                "--find-renames",
                "--untracked-files=all",
                "-z",
                working_directory=repository
            )
            if status_result.returncode != 0:
                raise GitStatusError("Could not inspect Git status after fetching.")
            status = _parse_porcelain_v2(status_result.stdout)

        working, counts = _worktree_summary(status["changes"])
        remote, ahead, behind = _sync_state(repository, status["upstream"], fetched)
        if fetched and ahead is None and behind is None:
            raise GitStatusError(
                "Fetch succeeded, but the configured upstream comparison is unavailable."
            )
        commit = _last_commit(repository) if status["oid"] else None
    except FileNotFoundError:
        rot_say("Git is not installed or is not available in PATH.")
        return 127
    except OSError as error:
        rot_say(f"Could not run Git.\n{error}")
        return 1
    except GitStatusError as error:
        rot_say(str(error))
        return 1

    branch = (
        f"Detached at {(status['oid'] or 'unknown')[:7]}"
        if status["detached"]
        else status["branch"] or "Unknown"
    )
    lines = [
        "ROTBOT GIT STATUS",
        "-----------------",
        f"Repository: {os.path.basename(repository)}",
        f"Branch:     {branch}",
        f"Upstream:   {status['upstream'] or 'Not configured'}",
        f"Working:    {working}",
        f"Remote:     {remote}"
    ]
    if fetched:
        lines.append("Fetched:    Remote comparison refreshed")
    if commit:
        commit_hash, relative_time, subject = commit
        lines.extend(("Last commit:", f"  {commit_hash} · {relative_time}", f"  {subject}"))
    else:
        lines.append("Last commit: None")

    next_action = None
    if counts["conflicted"]:
        next_action = "Resolve merge conflicts before continuing"
    elif status["changes"]:
        next_action = "Review changes with git diff"
    elif not status["oid"]:
        next_action = "Create the initial commit"
    elif status["detached"]:
        next_action = "Check out a branch to configure an upstream"
    elif not status["upstream"]:
        next_action = _upstream_suggestion(repository, status["branch"])
    elif ahead and behind:
        next_action = "Review the divergent history before pulling or pushing"
    elif behind:
        next_action = "rot pull"
    elif ahead:
        next_action = "rot push"
    if next_action:
        lines.extend(("", f"Next: {next_action}"))
    elif status["upstream"] and not fetched:
        lines.extend(("", "Verify: rot git status --fetch"))

    rot_say("\n".join(lines))
    return 0


def _read_input():
    try:
        return input("> ").strip()
    except EOFError:
        return ""


def _push_remote_url(repository, branch, upstream):
    remote_name = ""
    if branch:
        for key in (
            f"branch.{branch}.pushRemote",
            "remote.pushDefault",
            f"branch.{branch}.remote"
        ):
            configured = _capture_git("config", "--get", key, working_directory=repository)
            if configured.returncode == 0 and configured.stdout.strip():
                remote_name = configured.stdout.strip()
                break
    remotes = _capture_git("remote", working_directory=repository)
    names = remotes.stdout.split() if remotes.returncode == 0 else []
    if not remote_name and upstream:
        matches = [name for name in names if upstream.startswith(f"{name}/")]
        if matches:
            remote_name = max(matches, key=len)
    if not remote_name and len(names) == 1:
        remote_name = names[0]
    if not remote_name:
        return None
    if remote_name == ".":
        return str(repository)
    remote = _capture_git(
        "remote",
        "get-url",
        "--push",
        remote_name,
        working_directory=repository
    )
    return remote.stdout.strip() if remote.returncode == 0 else None


def _ssh_remote_host(remote_url):
    if not remote_url:
        return None
    match = re.match(r"^ssh://(?:[^@/]+@)?([^/:]+)(?::\d+)?/", remote_url)
    if match:
        return match.group(1)
    match = re.match(r"^(?:[^@/:]+@)?([^/:]+):[^/].+", remote_url)
    return match.group(1) if match else None


def _ssh_push_environment():
    environment = os.environ.copy()
    if "GIT_SSH_COMMAND" not in environment:
        command = ["ssh", "-o", "ConnectTimeout=10"]
        environment["GIT_SSH_COMMAND"] = shlex.join(command)
    return environment


def _check_remote_access(repository, remote_url, environment):
    return subprocess.run(
        ["git", "ls-remote", remote_url],
        capture_output=True,
        text=True,
        check=False,
        cwd=repository,
        env=environment
    )


def _preflight_ssh_push(repository, branch, upstream):
    remote_url = _push_remote_url(repository, branch, upstream)
    if remote_url is None:
        rot_say(
            "Could not determine the Git push remote. No Git changes were made.\n\n"
            "Configure an upstream or push remote, then retry."
        )
        return None
    host = _ssh_remote_host(remote_url)
    if host is None:
        return os.environ.copy()

    environment = _ssh_push_environment()
    try:
        access = _check_remote_access(repository, remote_url, environment)
    except FileNotFoundError:
        rot_say("Git or SSH is not installed or is not available in PATH.")
        return None
    if access.returncode != 0:
        detail = access.stderr.strip()
        rot_say(
            f"Could not authenticate to the SSH push remote at {host}. "
            "No Git changes were made.\n\n"
            + (f"{detail}\n\n" if detail else "")
            + "Check the host's SSH configuration and available identities, then retry."
        )
        return None
    rot_say(f"SSH push access verified for {host}.")
    return environment


def git_pull(args):
    rot_say("Running: git pull")

    try:
        result = subprocess.run(["git", "pull"], check=False)
    except FileNotFoundError:
        rot_say("Git is not installed or is not available in PATH.")
        return 127

    if result.returncode != 0:
        rot_say(f"git pull failed with exit code {result.returncode}.")
        return result.returncode

    rot_say("Pull complete.")
    return 0


def git_push(args, working_directory=None):
    command_directory = working_directory or os.getcwd()

    try:
        inside_repo = _capture_git(
            "rev-parse",
            "--is-inside-work-tree",
            working_directory=command_directory
        )
    except FileNotFoundError:
        rot_say("Git is not installed or is not available in PATH.")
        return 127

    if inside_repo.returncode != 0 or inside_repo.stdout.strip() != "true":
        rot_say("The current directory is not inside a Git repository.")
        return 1

    repository = _capture_git(
        "rev-parse",
        "--show-toplevel",
        working_directory=command_directory
    )
    if repository.returncode != 0:
        rot_say(f"Could not inspect the Git repository.\n{repository.stderr.strip()}")
        return 1
    repository_root = repository.stdout.strip()

    status = _capture_git(
        "status",
        "--short",
        working_directory=repository_root
    )
    branch = _capture_git(
        "branch",
        "--show-current",
        working_directory=repository_root
    )
    upstream = _capture_git(
        "rev-parse",
        "--abbrev-ref",
        "--symbolic-full-name",
        "@{upstream}",
        working_directory=repository_root
    )

    if status.returncode != 0:
        detail = status.stderr.strip()
        rot_say(f"Could not inspect the Git repository.\n{detail}")
        return 1

    provided_message = getattr(args, "message", None)

    branch_name = branch.stdout.strip() or "(detached HEAD)"
    upstream_name = (
        upstream.stdout.strip()
        if upstream.returncode == 0
        else "(not configured; git push may fail)"
    )
    changes = status.stdout.rstrip()
    if not changes:
        rot_say(
            "GIT PUSH PLAN\n"
            "-------------\n"
            f"Repository: {repository_root}\n"
            f"Directory:  {command_directory}\n"
            f"Branch:     {branch_name}\n"
            f"Upstream:   {upstream_name}\n"
            "Changes:    none to commit\n\n"
            "Action:\n"
            "  1. git push"
        )
        rot_say("Push existing commits? [y/N]")
        if _read_input().lower() not in {"y", "yes"}:
            rot_say("Push cancelled.")
            return PUSH_CANCELLED

        push_environment = _preflight_ssh_push(
            repository_root,
            branch.stdout.strip(),
            upstream.stdout.strip() if upstream.returncode == 0 else ""
        )
        if push_environment is None:
            return 1

        rot_say("Running: git push")
        result = subprocess.run(
            ["git", "push"],
            check=False,
            cwd=repository_root,
            env=push_environment
        )
        if result.returncode != 0:
            rot_say(f"git push failed with exit code {result.returncode}.")
            return result.returncode

        rot_say("Push complete.")
        return 0

    if provided_message:
        commit_message = provided_message.strip()
    else:
        rot_say("Enter a commit message:")
        commit_message = _read_input()

    if not commit_message:
        rot_say("Push cancelled: a commit message is required.")
        return 1

    indented_changes = "\n".join(f"  {line}" for line in changes.splitlines())
    commit_command = shlex.join(["git", "commit", "-m", commit_message])

    rot_say(
        "GIT PUSH PLAN\n"
        "-------------\n"
        f"Repository: {repository_root}\n"
        f"Directory:  {command_directory}\n"
        f"Branch:     {branch_name}\n"
        f"Upstream:   {upstream_name}\n"
        "Changes:\n"
        f"{indented_changes}\n\n"
        "Actions:\n"
        "  1. git add --all\n"
        f"  2. {commit_command}\n"
        "  3. git push"
    )

    rot_say("Proceed? [y/N]")
    confirmed = _read_input().lower()

    if confirmed not in {"y", "yes"}:
        rot_say("Push cancelled. No Git changes were made.")
        return PUSH_CANCELLED

    push_environment = _preflight_ssh_push(
        repository_root,
        branch.stdout.strip(),
        upstream.stdout.strip() if upstream.returncode == 0 else ""
    )
    if push_environment is None:
        return 1

    commands = (
        ["git", "add", "--all"],
        ["git", "commit", "-m", commit_message],
        ["git", "push"]
    )

    for command in commands:
        rot_say(f"Running: {shlex.join(command)}")
        result = subprocess.run(
            command,
            check=False,
            cwd=repository_root,
            env=push_environment if command == ["git", "push"] else None
        )

        if result.returncode != 0:
            rot_say(
                f"Command failed with exit code {result.returncode}:\n"
                f"{shlex.join(command)}"
            )
            return result.returncode

    rot_say("Push complete.")
    return 0


class GitStartError(Exception):
    pass


def _git_config_global_get(key):
    try:
        result = _capture_git("config", "--global", "--get", key)
    except FileNotFoundError:
        raise GitStartError("Git is not installed or is not available in PATH.")
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def _git_config_global_set(key, value):
    try:
        result = subprocess.run(
            ["git", "config", "--global", key, value],
            capture_output=True,
            text=True,
            check=False
        )
    except FileNotFoundError:
        return False
    return result.returncode == 0


def _machine_git_identity():
    return (
        _git_config_global_get("user.name"),
        _git_config_global_get("user.email")
    )


def _github_ssh_username(host="github.com", environment: dict[str, str] | None = None):
    command = [
        "ssh", "-T",
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=10",
        "-o", "StrictHostKeyChecking=accept-new",
        f"git@{host}"
    ]
    try:
        process = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=20,
            env=environment
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    combined = (process.stdout or "") + (process.stderr or "")
    match = re.search(r"Hi ([^\s!]+)! You've successfully authenticated", combined)
    return match.group(1) if match else None


def _git_remote_accessible(remote_url, environment: dict[str, str] | None = None):
    try:
        process = subprocess.run(
            ["git", "ls-remote", remote_url],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
            env=environment or _ssh_push_environment()
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False
    return process.returncode == 0


def _resolve_current_user():
    try:
        bindings = get_local_context_bindings()
    except ConfigError as error:
        raise GitStartError(str(error)) from None
    user = bindings.get("user")
    if user is None:
        raise GitStartError(
            "No Rot user is configured.\n"
            "Configure a user context before running Git setup."
        )
    try:
        person = entities.load_user_context(user)
    except entities.EntityContextError as error:
        raise GitStartError(str(error)) from None
    return person, entities.entity_directory(person)


def _discover_github_ssh_hosts() -> list[dict]:
    """
    Discover SSH aliases targeting github.com from the user's SSH config.
    
    Returns a list of dicts with keys:
    - alias: the Host alias (e.g., "github-rotbot")
    - hostname: the HostName (e.g., "github.com")
    - identity_files: list of IdentityFile paths (expanded but not read)
    
    This function is READ ONLY - it never modifies any files.
    """
    ssh_config_path = Path.home() / ".ssh" / "config"
    if not ssh_config_path.is_file():
        return []
    
    visited: set[Path] = set()
    aliases: list[dict] = []
    
    def parse_config_file(path: Path, depth: int = 0) -> None:
        # Safety limits
        if depth > 10:
            return
        if len(visited) > 50:
            return
        
        try:
            resolved = path.resolve()
        except (OSError, RuntimeError):
            return
        
        if resolved in visited:
            return
        visited.add(resolved)
        
        try:
            content = resolved.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            return
        
        current_host_patterns: list[str] = []
        current_hostname: str | None = None
        current_identity_files: list[str] = []
        
        for line in content.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            
            # Split directive and value
            parts = stripped.split(None, 1)
            if len(parts) != 2:
                continue
            
            directive, value = parts[0].lower(), parts[1].strip()
            
            if directive == "host":
                # New Host block - save previous if it had github.com
                if current_hostname == "github.com" and current_host_patterns:
                    for pattern in current_host_patterns:
                        # Only include literal aliases (no wildcards, no negations)
                        if not _is_wildcard_pattern(pattern):
                            aliases.append({
                                "alias": pattern,
                                "hostname": current_hostname,
                                "identity_files": list(current_identity_files)
                            })
                # Start new Host block
                current_host_patterns = value.split()
                current_hostname = None
                current_identity_files = []
            elif directive == "hostname":
                current_hostname = value
            elif directive == "identityfile":
                # Expand ~ but don't read the file
                expanded = os.path.expanduser(value)
                current_identity_files.append(expanded)
            elif directive == "include":
                # Process include directives
                include_paths = _expand_include_path(value, resolved.parent)
                for include_path in include_paths:
                    parse_config_file(include_path, depth + 1)
        
        # Handle last Host block in file
        if current_hostname == "github.com" and current_host_patterns:
            for pattern in current_host_patterns:
                if not _is_wildcard_pattern(pattern):
                    aliases.append({
                        "alias": pattern,
                        "hostname": current_hostname,
                        "identity_files": list(current_identity_files)
                    })
    
    parse_config_file(ssh_config_path)
    
    # Deduplicate by alias, preserving order
    seen = set()
    result = []
    for entry in aliases:
        alias = entry["alias"]
        if alias not in seen:
            seen.add(alias)
            result.append(entry)
    
    return result


def _is_wildcard_pattern(pattern: str) -> bool:
    """Check if a Host pattern contains wildcards or negations."""
    return any(c in pattern for c in "*?") or pattern.startswith("!")


def _expand_include_path(pattern: str, base_dir: Path) -> list[Path]:
    """Expand an Include path pattern with ~ and glob support."""
    try:
        # Handle ~ expansion
        expanded = os.path.expanduser(pattern)
        path = Path(expanded)
        
        # If relative, make it relative to the base directory
        if not path.is_absolute():
            path = (base_dir / path).resolve()
        
        # Handle glob patterns
        if "*" in pattern or "?" in pattern or "[" in pattern:
            try:
                parent = path.parent
                if parent.is_dir():
                    matches = list(parent.glob(path.name))
                    return [m.resolve() for m in matches if m.is_file()]
            except (OSError, ValueError):
                pass
            return []
        
        # Single file
        if path.is_file():
            return [path]
    except (OSError, ValueError, RuntimeError):
        pass
    return []


class _SshAgentContext:
    """Manages a temporary SSH agent lifecycle for a single Rot operation."""
    
    def __init__(self):
        self.agent_pid: int | None = None
        self.auth_sock: str | None = None
        self._owned = False
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.cleanup()
    
    def start_agent(self) -> bool:
        """Start a temporary ssh-agent and capture its environment."""
        try:
            result = subprocess.run(
                ["ssh-agent", "-s"],
                capture_output=True,
                text=True,
                check=False,
                timeout=10
            )
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            return False
        
        if result.returncode != 0:
            return False
        
        # Parse ssh-agent output safely (no eval, no shell=True)
        # Expected format:
        # SSH_AUTH_SOCK=/tmp/ssh-xxx/agent.xxx; export SSH_AUTH_SOCK;
        # SSH_AGENT_PID=12345; export SSH_AGENT_PID;
        # echo Agent pid 12345;
        for line in result.stdout.splitlines():
            line = line.strip()
            if line.startswith("SSH_AUTH_SOCK="):
                # Extract value before semicolon
                value = line[len("SSH_AUTH_SOCK="):].split(";")[0].strip()
                if value:
                    self.auth_sock = value
            elif line.startswith("SSH_AGENT_PID="):
                value = line[len("SSH_AGENT_PID="):].split(";")[0].strip()
                try:
                    self.agent_pid = int(value)
                except ValueError:
                    pass
        
        if self.auth_sock and self.agent_pid:
            self._owned = True
            return True
        return False
    
    def get_environment(self) -> dict[str, str] | None:
        """Get the environment dict for subprocesses."""
        if self.auth_sock:
            env = os.environ.copy()
            env["SSH_AUTH_SOCK"] = self.auth_sock
            if self.agent_pid:
                env["SSH_AGENT_PID"] = str(self.agent_pid)
            return env
        return None
    
    def cleanup(self) -> None:
        """Kill the temporary agent if we started it."""
        if self._owned and self.agent_pid:
            try:
                subprocess.run(
                    ["kill", str(self.agent_pid)],
                    capture_output=True,
                    check=False,
                    timeout=5
                )
            except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
                pass
            self._owned = False
            self.agent_pid = None
            self.auth_sock = None


def _has_usable_ssh_agent() -> bool:
    """Check if SSH_AUTH_SOCK is set and ssh-add -l succeeds."""
    if not os.environ.get("SSH_AUTH_SOCK"):
        return False
    try:
        result = subprocess.run(
            ["ssh-add", "-l"],
            capture_output=True,
            check=False,
            timeout=5
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False


def _ssh_add_key(identity_path: str, environment: dict[str, str] | None = None) -> bool:
    """Add a key to the SSH agent. Returns True on success."""
    if not Path(identity_path).exists():
        return False
    try:
        env = environment or os.environ.copy()
        result = subprocess.run(
            ["ssh-add", identity_path],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
            env=env
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False


def _try_load_github_key(
    identity_files: list[str],
    environment: dict[str, str] | None = None
) -> bool:
    """Try to load each configured IdentityFile into the SSH agent."""
    for identity_path in identity_files:
        if _ssh_add_key(identity_path, environment):
            return True
    return False


def _prompt(question):
    try:
        return input(f"{question}: ").strip()
    except EOFError:
        return ""


def _confirm(question, default_yes):
    suffix = "Y/n" if default_yes else "y/N"
    try:
        answer = input(f"{question} [{suffix}] ").strip().lower()
    except EOFError:
        answer = ""
    if not answer:
        return default_yes
    return answer in {"y", "yes"}


def _resolve_github_ssh_host(expected_username: str | None = None):
    """
    Resolve the GitHub SSH host for the current operation.
    
    Returns a tuple: (host, detected_username, verified, ssh_environment)
    - host: the SSH host to use (e.g., "github.com" or "github-rotbot")
    - detected_username: the GitHub username from SSH auth, or None
    - verified: True if SSH authentication succeeded
    - ssh_environment: dict with SSH_AUTH_SOCK/SSH_AGENT_PID for propagation, or None
    """
    # STEP 1: Try the normal host
    host = "github.com"
    detected = _github_ssh_username(host)
    if detected is not None:
        return host, detected, True, None
    
    # STEP 2: github.com failed, discover SSH aliases
    rot_say(f"GitHub SSH authentication via {host} failed.")
    
    # Discover candidates from SSH config (now returns list of dicts with identity_files)
    candidates = _discover_github_ssh_hosts()
    
    # STEP 3: Test discovered aliases, with agent/key loading if needed
    working_aliases: list[tuple[str, str, dict | None]] = []  # (alias, detected_username, ssh_env)
    
    for candidate in candidates:
        alias = candidate["alias"]
        identity_files = candidate.get("identity_files", [])
        
        # First try without any agent setup
        detected = _github_ssh_username(alias)
        if detected is not None:
            working_aliases.append((alias, detected, None))
            continue
        
        # If we have configured identity files, try to load them
        if identity_files:
            ssh_env = None
            agent_context = _SshAgentContext()
            
            try:
                # Check for existing usable agent
                if _has_usable_ssh_agent():
                    # Use existing agent
                    ssh_env = os.environ.copy()
                else:
                    # Start temporary agent
                    if agent_context.start_agent():
                        ssh_env = agent_context.get_environment()
                    else:
                        agent_context.cleanup()
                        agent_context = None
                
                if ssh_env:
                    # Try to load configured keys
                    if _try_load_github_key(identity_files, ssh_env):
                        rot_say("SSH key loaded into agent")
                        # Retry authentication with the agent
                        detected = _github_ssh_username(alias, ssh_env)
                        if detected is not None:
                            working_aliases.append((alias, detected, ssh_env))
                            continue
                
                # Clean up temporary agent if we created one
                if agent_context:
                    agent_context.cleanup()
            except Exception:
                if agent_context:
                    agent_context.cleanup()
    
    # STEP 4: Select a usable candidate
    if expected_username:
        # Filter by expected username
        matching = [(a, u, e) for a, u, e in working_aliases if u == expected_username]
        if len(matching) == 1:
            alias, detected, ssh_env = matching[0]
            rot_say(f"Found GitHub SSH configuration:\n  {alias} -> github.com")
            return alias, detected, True, ssh_env
        elif len(matching) > 1:
            # Multiple aliases work for the expected user - present choices
            return _prompt_for_alias_choice(matching, expected_username)
    else:
        # No expected username - check if exactly one works
        if len(working_aliases) == 1:
            alias, detected, ssh_env = working_aliases[0]
            rot_say(f"Found GitHub SSH configuration:\n  {alias} -> github.com")
            return alias, detected, True, ssh_env
        elif len(working_aliases) > 1:
            # Multiple aliases work for different users - present choices
            return _prompt_for_alias_choice(working_aliases, None)
    
    # STEP 5: Fall back to manual prompt
    host = _prompt_value("github.com", "SSH host or alias [github.com]")
    detected = _github_ssh_username(host)
    if detected is not None:
        return host, detected, True, None
    
    return host, None, False, None


def _prompt_for_alias_choice(
    working_aliases: list[tuple[str, str, dict | None]], 
    expected_username: str | None
) -> tuple[str, str | None, bool, dict | None]:
    """Present numbered choices for multiple working aliases."""
    if expected_username:
        rot_say(f"Multiple GitHub SSH configurations work for {expected_username}:")
    else:
        rot_say("Multiple GitHub SSH configurations work:")
    
    for i, (alias, username, _) in enumerate(working_aliases, 1):
        rot_say(f"  {i}. {alias} -> github.com (authenticates as {username})")
    
    prompt = "Use [1]: "
    try:
        answer = input(prompt).strip()
    except EOFError:
        answer = ""
    
    if not answer:
        choice = 1
    else:
        try:
            choice = int(answer)
        except ValueError:
            choice = 1
    
    if 1 <= choice <= len(working_aliases):
        alias, detected, ssh_env = working_aliases[choice - 1]
        return alias, detected, True, ssh_env
    
    # Invalid choice, fall back
    host = _prompt_value("github.com", "SSH host or alias [github.com]")
    detected = _github_ssh_username(host)
    if detected is not None:
        return host, detected, True, None
    
    return host, None, False, None


def _ensure_default_branch_main():
    if _git_config_global_get("init.defaultBranch") == "main":
        return True
    return _git_config_global_set("init.defaultBranch", "main")


def _ensure_machine_identity(name, email):
    machine_name, machine_email = _machine_git_identity()
    if machine_name == name and machine_email == email:
        return True, True
    rot_say("Rot user Git identity:")
    rot_say(f"  {name} <{email}>")
    rot_say("Current machine Git identity:")
    rot_say(f"  {machine_name or '(unset)'} <{machine_email or '(unset)'}>")
    unset = not machine_name and not machine_email
    if unset:
        if not _confirm(
            "Configure this machine with this Git identity?", default_yes=True
        ):
            return False, False
    elif not _confirm(
        "Replace this machine's Git identity?", default_yes=False
    ):
        return False, False
    name_ok = _git_config_global_set("user.name", name)
    email_ok = _git_config_global_set("user.email", email)
    return True, name_ok and email_ok


def _gather_git_identity(loaded, user_name):
    stored_name = loaded.git_name if loaded else ""
    stored_email = loaded.git_email if loaded else ""
    if stored_name and stored_email:
        return stored_name, stored_email
    machine_name, machine_email = _machine_git_identity()
    if not stored_name and not stored_email and machine_name and machine_email:
        rot_say("Existing Git identity detected:")
        rot_say(f"  Name:  {machine_name}")
        rot_say(f"  Email: {machine_email}")
        if _confirm(f"Save this identity to Rot user '{user_name}'?", default_yes=True):
            return machine_name, machine_email
        machine_name, machine_email = "", ""
    name = stored_name or machine_name
    email = stored_email or machine_email
    if not name:
        rot_say(
            "Git author name (the name written into commits, "
            "not the GitHub username):"
        )
        name = _prompt("Git author name")
    if not email:
        rot_say("Git author email:")
        email = _prompt("Git author email")
    return name or machine_name, email or machine_email


def _github_identity(loaded):
    stored = loaded.github_username if loaded else ""
    host, detected, verified, ssh_env = _resolve_github_ssh_host(stored or None)
    if detected is not None:
        rot_say(f"✓ GitHub SSH authentication verified as {detected}")
        if stored and stored != detected:
            rot_say("GitHub account mismatch.")
            rot_say(f"  Portable GitHub account:   {stored}")
            rot_say(f"  This machine authenticated as: {detected}")
            if _confirm(
                "Replace the stored GitHub username with the verified value?",
                default_yes=False
            ):
                return detected, True, host, ssh_env
            return stored, False, host, ssh_env
        username = stored or detected
        return username, True, host, ssh_env
    rot_say("GitHub SSH authentication could not be verified.")
    if stored:
        rot_say(f"  Using stored username: {stored}")
        return stored, False, host, ssh_env
    return _prompt("GitHub username"), False, host, ssh_env


def _setup_flow(person, user_directory):
    try:
        loaded = accounts.load_accounts(user_directory)
    except accounts.AccountError as error:
        rot_say(str(error))
        return 1

    name, email = _gather_git_identity(loaded, person.name)
    username, github_verified, ssh_host, ssh_env = _github_identity(loaded)
    if not name or not email:
        rot_say("Git author identity is incomplete. No changes were made.")
        return 1
    if not username:
        rot_say("A GitHub username is required. No changes were made.")
        return 1

    if loaded and loaded.github_default_visibility:
        visibility = loaded.github_default_visibility
    else:
        visibility = _prompt_value(
            "private", "Default GitHub visibility [private]"
        )
    if visibility not in {"private", "public"}:
        rot_say(f"Invalid visibility: {visibility}")
        return 1

    rot_say("Git author:")
    rot_say(f"  {name} <{email}>")
    rot_say("GitHub account:")
    rot_say(f"  {username}")
    rot_say("SSH authentication:")
    rot_say("  ✓ verified" if github_verified else "  not verified on this machine")
    if not _confirm(
        f"Save this identity for Rot user '{person.name}'?",
        default_yes=True
    ):
        rot_say("Git setup cancelled. No changes were made.")
        return 1

    try:
        accounts.write_accounts(
            user_directory,
            accounts.AccountFile(
                git_name=name,
                git_email=email,
                github_username=username,
                github_default_visibility=visibility
            )
        )
    except accounts.AccountError as error:
        rot_say(str(error))
        return 1
    rot_say("✓ saved user Git identity")
    if not github_verified:
        rot_say("GitHub account saved.")
        rot_say("SSH authentication is not verified on this machine.")

    agreed, configured = _ensure_machine_identity(name, email)
    if not agreed:
        rot_say("Machine Git identity was not changed.")
        return 0
    if not configured:
        rot_say("Could not configure Git on this machine.")
        return 1
    if not _ensure_default_branch_main():
        rot_say("Could not configure Git on this machine.")
        return 1
    rot_say("✓ configured Git on this machine")
    return 0


def git_setup(args):
    try:
        person, user_directory = _resolve_current_user()
    except GitStartError as error:
        rot_say(str(error))
        return 1
    return _setup_flow(person, user_directory)


def _prompt_value(default, prompt):
    try:
        return input(f"{prompt}: ").strip() or default
    except EOFError:
        return default


def _accounts_complete(loaded):
    return (
        loaded is not None
        and loaded.git_name
        and loaded.git_email
        and loaded.github_username
    )


def git_start(args, working_directory=None):
    command_directory = working_directory or os.getcwd()

    try:
        inside = _capture_git(
            "rev-parse",
            "--is-inside-work-tree",
            working_directory=command_directory
        )
    except FileNotFoundError:
        rot_say("Git is not installed or is not available in PATH.")
        return 127
    except OSError as error:
        rot_say(f"Could not run Git.\n{error}")
        return 1

    if inside.returncode == 0 and inside.stdout.strip() == "true":
        rot_say("The current directory is already inside a Git repository.")
        return 1

    try:
        person, user_directory = _resolve_current_user()
    except GitStartError as error:
        rot_say(str(error))
        return 1

    try:
        loaded = accounts.load_accounts(user_directory)
    except accounts.AccountError as error:
        rot_say(str(error))
        return 1

    if not _accounts_complete(loaded):
        rot_say("Git setup is incomplete for this Rot user.")
        if not _confirm("Configure it now?", default_yes=True):
            rot_say("No Git repository was created.")
            return 1
        if _setup_flow(person, user_directory) != 0:
            return 1
        try:
            loaded = accounts.load_accounts(user_directory)
        except accounts.AccountError as error:
            rot_say(str(error))
            return 1
        if not _accounts_complete(loaded):
            rot_say("Git setup is still incomplete. No Git repository was created.")
            return 1

    agreed, configured = _ensure_machine_identity(loaded.git_name, loaded.git_email)
    if not agreed:
        rot_say("No Git repository was created.")
        return 1
    if not configured:
        rot_say("Could not configure Git on this machine.\nNo Git repository was created.")
        return 1

    ssh_host, detected_username, ssh_verified, ssh_env = _resolve_github_ssh_host(loaded.github_username)
    if not ssh_verified or detected_username is None:
        rot_say(
            "GitHub SSH authentication is not configured on this machine.\n"
            "No Git repository was created."
        )
        return 1
    if detected_username != loaded.github_username:
        rot_say("GitHub account mismatch.")
        rot_say(f"  Portable GitHub account:   {loaded.github_username}")
        rot_say(f"  This machine authenticated as: {detected_username}")
        rot_say("No Git repository was created.")
        return 1
    rot_say("GitHub account:")
    rot_say(f"  {loaded.github_username}")
    rot_say("SSH host:")
    rot_say(f"  {ssh_host}")
    rot_say("✓ SSH authentication verified")

    default_name = Path(os.path.abspath(command_directory)).name
    visibility = loaded.github_default_visibility or "private"
    rot_say("Git author:")
    rot_say(f"  {loaded.git_name} <{loaded.git_email}>")
    repository_name = _prompt_value(default_name, f"Repository name [{default_name}]")
    visibility = _prompt_value(visibility, f"Visibility [{visibility}]")
    if visibility not in {"private", "public"}:
        rot_say(f"Invalid visibility: {visibility}")
        return 1

    remote_url = f"git@{ssh_host}:{loaded.github_username}/{repository_name}.git"
    found = False
    if _git_remote_accessible(remote_url, ssh_env):
        rot_say(
            f"✓ found existing GitHub repository "
            f"{loaded.github_username}/{repository_name}"
        )
        found = True
    else:
        rot_say("Create an EMPTY GitHub repository:")
        rot_say(f"  Owner:       {loaded.github_username}")
        rot_say(f"  Repository:  {repository_name}")
        rot_say(f"  Visibility:  {visibility}")
        rot_say("")
        rot_say("Do not initialize it with a README, .gitignore, or license.")
        rot_say("")
        rot_say("Press Enter when the repository exists.")
        rot_say("Type q to cancel.")
        answer = _read_input().strip().lower()
        if answer in {"q", "quit", "exit", "cancel"}:
            rot_say("Cancelled. No Git repository was created.")
            return 1
        if not _git_remote_accessible(remote_url, ssh_env):
            rot_say("Could not verify GitHub repository:")
            rot_say(f"  {remote_url}")
            return 1
        rot_say(
            f"✓ found GitHub repository {loaded.github_username}/{repository_name}"
        )
        found = True

    if not found:
        return 1

    git_directory = Path(command_directory) / ".git"
    created_git = not git_directory.exists()
    local_commands = (
        ["git", "init", "-b", "main"],
        ["git", "add", "-A"],
        ["git", "commit", "-m", "Initial commit"],
        ["git", "remote", "add", "origin", remote_url]
    )
    try:
        for command in local_commands:
            result = subprocess.run(command, check=False, cwd=command_directory)
            if result.returncode != 0:
                if created_git and git_directory.exists():
                    shutil.rmtree(git_directory, ignore_errors=True)
                    rot_say("! local initialization failed and was rolled back")
                else:
                    rot_say(
                        f"Command failed with exit code {result.returncode}:\n"
                        f"{shlex.join(command)}"
                    )
                return result.returncode
    except FileNotFoundError:
        rot_say("Git is not installed or is not available in PATH.")
        return 127

    rot_say("✓ initialized git repository")
    rot_say("✓ created initial commit")
    rot_say("✓ added origin")

    # Combine ssh_env with push environment for the final push
    push_env = _ssh_push_environment()
    if ssh_env:
        # Preserve any existing GIT_SSH_COMMAND from push_env
        push_env.update(ssh_env)
    
    push_result = subprocess.run(
        ["git", "push", "-u", "origin", "main"],
        check=False,
        cwd=command_directory,
        env=push_env
    )
    if push_result.returncode != 0:
        rot_say("! push failed")
        rot_say("  local repository retained")
        rot_say(f"  remote: {remote_url}")
        return push_result.returncode

    rot_say("✓ pushed main")
    return 0
