import os
import re
import shlex
import subprocess

from gui import rot_say
from opencode_runner import stream_opencode


PUSH_CANCELLED = object()


def _capture_git(*args, working_directory=None):
    return subprocess.run(
        ["git", *args],
        capture_output=True,
        text=True,
        check=False,
        cwd=working_directory
    )


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
    provided_message = getattr(args, "message", None)
    if review_note and not review_requested:
        rot_say("--note requires --review for a push command.")
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

        rot_say("Running: git push")
        result = subprocess.run(
            ["git", "push"],
            check=False,
            cwd=command_directory
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
        rot_say("Starting streamed OpenCode review...")
        review_returncode, review_output, review_elapsed = stream_opencode(
            review_prompt,
            "Rotbot is still reviewing...",
            command_directory
        )
        rot_say(f"OpenCode review finished in {review_elapsed:.1f}s.")

        if review_returncode != 0:
            rot_say(
                f"OpenCode review failed with exit code {review_returncode}."
            )
            return review_returncode

        if not review_output.strip():
            rot_say("OpenCode returned an empty review.")

        suggested_message = _suggested_commit_message(review_output)
        if suggested_message:
            rot_say(f"Commit suggestion received: {suggested_message}")
        else:
            rot_say("OpenCode did not return a usable commit message suggestion.")

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
            cwd=command_directory
        )

        if result.returncode != 0:
            rot_say(
                f"Command failed with exit code {result.returncode}:\n"
                f"{shlex.join(command)}"
            )
            return result.returncode

    rot_say("Push complete.")
    return 0
