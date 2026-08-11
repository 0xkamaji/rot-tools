import os
from pathlib import Path
import re
import shlex
import subprocess

from rotbot.agents.runner import stream_agent
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


def _suggested_commit_message(review_output):
    match = re.search(
        r"^\s*SUGGESTED_COMMIT_MESSAGE:\s*(.+?)\s*$",
        review_output,
        re.MULTILINE
    )
    return match.group(1).strip("`\"'") if match else ""


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


def _ssh_push_environment(default_key=None):
    environment = os.environ.copy()
    environment["GIT_TERMINAL_PROMPT"] = "0"
    if "GIT_SSH_COMMAND" not in environment:
        command = ["ssh"]
        if default_key is not None:
            command.extend(["-i", str(default_key), "-o", "IdentitiesOnly=yes"])
        command.extend(["-o", "BatchMode=yes", "-o", "ConnectTimeout=10"])
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

    default_key = Path.home() / ".ssh" / "id_ed25519"
    environment = _ssh_push_environment()
    direct_key = False
    try:
        identities = subprocess.run(
            ["ssh-add", "-l"],
            capture_output=True,
            text=True,
            check=False
        )
    except FileNotFoundError:
        rot_say("OpenSSH tools are required to use an SSH Git remote.")
        return None

    if identities.returncode == 1:
        if default_key.is_file():
            rot_say(
                "SSH agent has no identities. Loading the default key for one hour:\n"
                f"{default_key}"
            )
            try:
                loaded = subprocess.run(
                    ["ssh-add", "-t", "1h", str(default_key)],
                    check=False
                )
            except FileNotFoundError:
                rot_say("OpenSSH tools are required to load an SSH key.")
                return None
            if loaded.returncode != 0:
                if "GIT_SSH_COMMAND" in os.environ:
                    rot_say(
                        "Could not load the default SSH key. No Git changes were made.\n\n"
                        f"Run in an interactive terminal:\n  ssh-add {default_key}"
                    )
                    return None
                environment = _ssh_push_environment(default_key)
                direct_key = True
        else:
            rot_say(
                "SSH agent has no identities and the default key does not exist:\n"
                f"{default_key}"
            )
            return None
    elif identities.returncode != 0:
        if default_key.is_file() and "GIT_SSH_COMMAND" not in os.environ:
            rot_say("SSH agent is unavailable. Testing the default key directly.")
            environment = _ssh_push_environment(default_key)
            direct_key = True
        else:
            rot_say(
                "Could not access the SSH agent. No Git changes were made.\n\n"
                "Start an SSH agent and load a key before pushing."
            )
            return None

    try:
        access = _check_remote_access(repository, remote_url, environment)
    except FileNotFoundError:
        rot_say("Git or SSH is not installed or is not available in PATH.")
        return None
    if (
        access.returncode != 0
        and not direct_key
        and default_key.is_file()
        and "GIT_SSH_COMMAND" not in os.environ
    ):
        direct_environment = _ssh_push_environment(default_key)
        try:
            direct_access = _check_remote_access(
                repository,
                remote_url,
                direct_environment
            )
        except FileNotFoundError:
            direct_access = None
        if direct_access is not None and direct_access.returncode == 0:
            access = direct_access
            environment = direct_environment
            direct_key = True
    if access.returncode != 0:
        detail = access.stderr.strip()
        rot_say(
            f"Could not authenticate to the SSH push remote at {host}. "
            "No Git changes were made.\n\n"
            + (f"{detail}\n\n" if detail else "")
            + "Load the correct key, then retry:\n"
            f"  ssh-add {Path.home() / '.ssh' / 'id_ed25519'}"
        )
        return None
    method = "default key" if direct_key else "SSH agent"
    rot_say(f"SSH push access verified for {host} using the {method}.")
    return environment


def _edit_commit_message(suggested_message):
    try:
        import readline
    except ImportError:
        return _read_input()

    readline.set_startup_hook(lambda: readline.insert_text(suggested_message))
    try:
        return _read_input()
    finally:
        readline.set_startup_hook()


def _choose_commit_message(suggested_message):
    while True:
        rot_say(
            f"Suggested commit message:\n{suggested_message}\n\n"
            "[A]ccept, [E]dit, or [R]eplace?"
        )
        choice = _read_input().lower()

        if choice in {"", "a", "accept"}:
            return suggested_message
        if choice in {"e", "edit"}:
            rot_say("Edit the prefilled commit message, then press Enter:")
            return _edit_commit_message(suggested_message)
        if choice in {"r", "replace"}:
            rot_say("Enter a replacement commit message:")
            return _read_input()

        rot_say("Please choose accept, edit, or replace.")


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


def git_push(args, working_directory=None, review_context=None):
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
    status = _capture_git(
        "status",
        "--short",
        working_directory=command_directory
    )
    branch = _capture_git(
        "branch",
        "--show-current",
        working_directory=command_directory
    )
    upstream = _capture_git(
        "rev-parse",
        "--abbrev-ref",
        "--symbolic-full-name",
        "@{upstream}",
        working_directory=command_directory
    )

    if repository.returncode != 0 or status.returncode != 0:
        detail = repository.stderr.strip() or status.stderr.strip()
        rot_say(f"Could not inspect the Git repository.\n{detail}")
        return 1

    review_requested = getattr(args, "review", False)
    review_note = getattr(args, "note", None)
    review_agent = getattr(args, "agent", None)
    provided_message = getattr(args, "message", None)
    if review_note and not review_requested:
        rot_say("--note requires --review for a push command.")
        return 2
    if review_agent and not review_requested:
        rot_say("--agent requires --review for a push command.")
        return 2

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
            f"Repository: {repository.stdout.strip()}\n"
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
            repository.stdout.strip(),
            branch.stdout.strip(),
            upstream.stdout.strip() if upstream.returncode == 0 else ""
        )
        if push_environment is None:
            return 1

        rot_say("Running: git push")
        result = subprocess.run(
            ["git", "push"],
            check=False,
            cwd=command_directory,
            env=push_environment
        )
        if result.returncode != 0:
            rot_say(f"git push failed with exit code {result.returncode}.")
            return result.returncode

        rot_say("Push complete.")
        return 0

    suggested_message = ""

    if review_requested:
        rot_say("Running: git status --short\nRunning: git diff HEAD")
        diff = _capture_git("diff", "HEAD", working_directory=command_directory)

        if diff.returncode != 0:
            rot_say(f"Could not read the Git diff.\n{diff.stderr.strip()}")
            return 1

        review_prompt = (
            "Review the following uncommitted Git changes. Do not modify any "
            "files. Report bugs, risks, behavioral regressions, and missing "
            "tests first, ordered by severity with file references. If there "
            "are no findings, say so explicitly. Be thorough and include "
            "sections for findings, testing gaps, and a recommendation. You "
            "may inspect untracked files listed in the status from the current "
            "repository, but keep the entire review read-only. End with exactly "
            "one plain-text line in this format, with no Markdown around the "
            "message:\nSUGGESTED_COMMIT_MESSAGE: <concise commit message>\n\n"
            f"Task context: {review_context or 'Commit and push this Git repository.'}\n\n"
            f"Git status:\n{changes}\n\n"
            f"Git diff:\n{diff.stdout.rstrip() or '(no tracked diff)'}"
            + (
                f"\n\nAdditional user note:\n{review_note}"
                if review_note
                else ""
            )
        )

        changed_paths = len(changes.splitlines())
        diff_lines = len(diff.stdout.splitlines())
        rot_say(
            "Review input collected.\n"
            f"Changed paths: {changed_paths}\n"
            f"Diff lines:    {diff_lines}"
        )
        rot_say("Starting streamed AI review...")
        review_returncode, review_output, review_elapsed = stream_agent(
            review_prompt,
            "Rotbot is still reviewing...",
            command_directory,
            agent_name=review_agent
        )
        rot_say(f"AI review finished in {review_elapsed:.1f}s.")

        if review_returncode != 0:
            rot_say(
                f"AI review failed with exit code {review_returncode}."
            )
            return review_returncode

        if not review_output.strip():
            rot_say("The AI agent returned an empty review.")

        suggested_message = _suggested_commit_message(review_output)
        if suggested_message:
            rot_say(f"Commit suggestion received: {suggested_message}")
        else:
            rot_say("The AI agent did not return a usable commit message suggestion.")

        rot_say("Continue with the commit and push? [y/N]")
        confirmed = _read_input().lower()

        if confirmed not in {"y", "yes"}:
            rot_say("Push cancelled. No Git changes were made.")
            return PUSH_CANCELLED

    if provided_message:
        commit_message = provided_message.strip()
    elif suggested_message:
        commit_message = _choose_commit_message(suggested_message)
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
        f"Repository: {repository.stdout.strip()}\n"
        f"Directory:  {command_directory}\n"
        f"Branch:     {branch_name}\n"
        f"Upstream:   {upstream_name}\n"
        "Changes:\n"
        f"{indented_changes}\n\n"
        "Actions:\n"
        "  1. git add .\n"
        f"  2. {commit_command}\n"
        "  3. git push"
    )

    if not review_requested:
        rot_say("Proceed? [y/N]")
        confirmed = _read_input().lower()

        if confirmed not in {"y", "yes"}:
            rot_say("Push cancelled. No Git changes were made.")
            return PUSH_CANCELLED

    push_environment = _preflight_ssh_push(
        repository.stdout.strip(),
        branch.stdout.strip(),
        upstream.stdout.strip() if upstream.returncode == 0 else ""
    )
    if push_environment is None:
        return 1

    commands = (
        ["git", "add", "."],
        ["git", "commit", "-m", commit_message],
        ["git", "push"]
    )

    for command in commands:
        rot_say(f"Running: {shlex.join(command)}")
        result = subprocess.run(
            command,
            check=False,
            cwd=command_directory,
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
