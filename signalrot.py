import os
from pathlib import Path
import shlex
import subprocess
from time import perf_counter
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from agents.runner import stream_agent
from git_commands import PUSH_CANCELLED, git_push
from gui import rot_say


DEFAULT_REPO = "/home/kamaji/github/signalrot"
DEFAULT_WEB_ROOT = "/var/www/signalrot"
GIT_EXCLUDES = (
    ".git",
    ".github",
    ".gitignore",
    ".gitattributes",
    ".gitmodules"
)


def _repo_path():
    return Path(os.environ.get("SIGNALROT_REPO", DEFAULT_REPO)).expanduser().resolve()


def _web_root():
    return Path(os.environ.get("SIGNALROT_WEB_ROOT", DEFAULT_WEB_ROOT)).expanduser().resolve()


def _capture(command, working_directory):
    return subprocess.run(
        command,
        cwd=working_directory,
        capture_output=True,
        text=True,
        check=False
    )


def _rsync_command(repository, web_root, dry_run=False, sudo=False):
    excludes = [item for pattern in GIT_EXCLUDES for item in ("--exclude", pattern)]
    command = [
        "rsync",
        "--archive",
        "--delete",
        "--delete-excluded"
    ]

    if dry_run:
        command.extend(("--dry-run", "--itemize-changes"))

    command.extend((
        *excludes,
        f"{repository}/",
        f"{web_root}/"
    ))
    return ["sudo", *command] if sudo else command


def _confirm(message):
    rot_say(f"{message} [y/N]")
    try:
        answer = input("> ").strip().lower()
    except EOFError:
        answer = ""
    return answer in {"y", "yes"}


def _validate_repo(repository):
    if not repository.is_dir():
        rot_say(f"Signal Rot repository not found:\n{repository}")
        return False

    try:
        result = _capture(
            ["git", "rev-parse", "--is-inside-work-tree"],
            repository
        )
    except FileNotFoundError:
        rot_say("Git is not installed or is not available in PATH.")
        return False

    if result.returncode != 0 or result.stdout.strip() != "true":
        rot_say(f"Signal Rot path is not a Git repository:\n{repository}")
        return False

    return True


def _review_task(prompt, working_directory, activity, agent_name=None):
    rot_say("Starting streamed AI review...")
    returncode, output, elapsed = stream_agent(
        prompt,
        activity,
        working_directory,
        agent_name=agent_name
    )
    rot_say(f"AI review finished in {elapsed:.1f}s.")

    if returncode != 0:
        rot_say(f"AI review failed with exit code {returncode}.")
        return returncode
    if not output.strip():
        rot_say("The AI agent returned an empty review.")
        return 1

    return 0


def sr_status(args):
    url = "https://signalrot.net"
    request = Request(url, headers={"User-Agent": "rotbot/1.0"})
    started_at = perf_counter()

    try:
        with urlopen(request, timeout=10) as response:
            elapsed_ms = round((perf_counter() - started_at) * 1000)
            rot_say(
                "SIGNAL ROT STATUS\n"
                "-----------------\n"
                f"Site:     {url}\n"
                "State:    ONLINE\n"
                f"HTTP:     {response.status}\n"
                f"Response: {elapsed_ms} ms"
            )
    except HTTPError as error:
        elapsed_ms = round((perf_counter() - started_at) * 1000)
        rot_say(
            "SIGNAL ROT STATUS\n"
            "-----------------\n"
            f"Site:     {url}\n"
            "State:    ERROR\n"
            f"HTTP:     {error.code}\n"
            f"Response: {elapsed_ms} ms"
        )
    except (URLError, TimeoutError) as error:
        rot_say(
            "SIGNAL ROT STATUS\n"
            "-----------------\n"
            f"Site:     {url}\n"
            "State:    OFFLINE\n"
            f"Reason:   {error.reason if isinstance(error, URLError) else error}"
        )


def sr_pull(args):
    review_requested = getattr(args, "review", False)
    review_note = getattr(args, "note", None)
    review_agent = getattr(args, "agent", None)
    if review_note and not review_requested:
        rot_say("--note requires --review for Signal Rot pull.")
        return 2
    if review_agent and not review_requested:
        rot_say("--agent requires --review for Signal Rot pull.")
        return 2

    repository = _repo_path()
    if not _validate_repo(repository):
        return 1

    status = _capture(["git", "status", "--short"], repository)
    if status.returncode != 0:
        rot_say(f"Could not inspect Signal Rot changes.\n{status.stderr.strip()}")
        return status.returncode
    if status.stdout.strip():
        rot_say(
            "Pull cancelled because the Signal Rot repository has local changes.\n"
            f"{status.stdout.rstrip()}"
        )
        return 1

    rot_say(f"Fetching Signal Rot updates from GitHub...\nRepository: {repository}")
    fetch = subprocess.run(["git", "fetch"], cwd=repository, check=False)
    if fetch.returncode != 0:
        rot_say(f"git fetch failed with exit code {fetch.returncode}.")
        return fetch.returncode

    upstream = _capture(["git", "rev-parse", "@{upstream}"], repository)
    if upstream.returncode != 0:
        rot_say("The current Signal Rot branch does not have an upstream branch.")
        return 1

    incoming = _capture(
        ["git", "log", "--oneline", "HEAD..@{upstream}"],
        repository
    )
    if incoming.returncode != 0:
        rot_say(f"Could not inspect incoming commits.\n{incoming.stderr.strip()}")
        return incoming.returncode
    if not incoming.stdout.strip():
        rot_say("Signal Rot is already up to date with GitHub.")
        return 0

    diff = _capture(["git", "diff", "HEAD..@{upstream}"], repository)
    if diff.returncode != 0:
        rot_say(f"Could not inspect incoming changes.\n{diff.stderr.strip()}")
        return diff.returncode

    rot_say(
        "SIGNAL ROT PULL PLAN\n"
        "--------------------\n"
        f"Repository: {repository}\n"
        "Command:    git pull --ff-only\n"
        "Incoming commits:\n"
        f"{incoming.stdout.rstrip()}"
    )

    if review_requested:
        review_prompt = (
            "Review the incoming Signal Rot website changes before they are "
            "pulled from GitHub. Keep the review read-only and do not modify "
            "files. Identify bugs, regressions, deployment risks, and missing "
            "tests, ordered by severity. If there are no findings, say so.\n\n"
            f"Repository: {repository}\n"
            f"Incoming commits:\n{incoming.stdout.rstrip()}\n\n"
            f"Incoming diff:\n{diff.stdout.rstrip()}"
            + (
                f"\n\nAdditional user note:\n{review_note}"
                if review_note
                else ""
            )
        )
        review_result = _review_task(
            review_prompt,
            repository,
            "Rotbot is still reviewing the pull...",
            review_agent
        )
        if review_result != 0:
            return review_result

    if not _confirm("Pull these changes into the Signal Rot repository?"):
        rot_say("Signal Rot pull cancelled. No working files were changed.")
        return 0

    pull = subprocess.run(
        ["git", "pull", "--ff-only"],
        cwd=repository,
        check=False
    )
    if pull.returncode != 0:
        rot_say(f"git pull failed with exit code {pull.returncode}.")
        return pull.returncode

    rot_say("Signal Rot repository updated from GitHub.")
    return 0


def sr_push(args):
    repository = _repo_path()
    if not _validate_repo(repository):
        return 1

    rot_say(f"Preparing Signal Rot changes for GitHub.\nRepository: {repository}")
    return git_push(
        args,
        str(repository),
        "Commit and push the Signal Rot website source to GitHub."
    )


def sr_diff(args):
    repository = _repo_path()
    web_root = _web_root()
    if not _validate_repo(repository):
        return 1
    if not web_root.is_dir():
        rot_say(f"Signal Rot web root not found:\n{web_root}")
        return 1

    dry_run_command = _rsync_command(repository, web_root, dry_run=True)
    rot_say(
        "Comparing the Signal Rot repository with the live Caddy web root...\n"
        f"Repository: {repository}\n"
        f"Live site:  {web_root}"
    )
    try:
        dry_run = _capture(dry_run_command, repository)
    except FileNotFoundError:
        rot_say("rsync is not installed or is not available in PATH.")
        return 127

    if dry_run.returncode != 0:
        detail = dry_run.stderr.strip() or dry_run.stdout.strip()
        rot_say(f"Signal Rot comparison failed.\n{detail}")
        return dry_run.returncode

    planned_changes = dry_run.stdout.rstrip() or "(no deployment changes)"
    rot_say(
        "SIGNAL ROT DEPLOYMENT DIFF\n"
        "--------------------------\n"
        "Direction:   repository -> live web root\n"
        "Git data:    excluded\n"
        "Stale files: would be deleted\n"
        "Planned changes:\n"
        f"{planned_changes}"
    )

    note = getattr(args, "note", None)
    prompt = (
        "Produce a read-only comparison of the Signal Rot GitHub repository "
        "and the live Caddy web root. Do not modify files. Inspect both "
        "directories as needed and explain exactly what a publish would add, "
        "update, overwrite, or delete. Identify live-only edits, missing assets, "
        "secret or development files that could be exposed, and deployment "
        "risks. Distinguish repository content from live content and cite paths. "
        "Begin with 'SIGNAL ROT DIFF REPORT'.\n\n"
        f"Repository source: {repository}\n"
        f"Live destination: {web_root}\n"
        f"rsync dry-run:\n{planned_changes}"
        + (
            f"\n\nAdditional user note:\n{note}"
            if note
            else ""
        )
    )
    review_result = _review_task(
        prompt,
        repository,
        "Rotbot is still comparing Signal Rot...",
        getattr(args, "agent", None)
    )
    if review_result != 0:
        return review_result

    rot_say("Signal Rot deployment comparison complete. No files were changed.")
    return 0


def sr_publish(args):
    review_requested = getattr(args, "review", False)
    review_note = getattr(args, "note", None)
    review_agent = getattr(args, "agent", None)
    if review_note and not review_requested:
        rot_say("--note requires --review for Signal Rot publish.")
        return 2
    if review_agent and not review_requested:
        rot_say("--agent requires --review for Signal Rot publish.")
        return 2

    repository = _repo_path()
    web_root = _web_root()
    if not _validate_repo(repository):
        return 1
    if not web_root.is_dir():
        rot_say(f"Signal Rot web root not found:\n{web_root}")
        return 1

    rot_say("Signal Rot publish begins by pushing the repository to GitHub.")
    push_result = sr_push(args)
    if push_result is PUSH_CANCELLED:
        rot_say("Signal Rot publish cancelled because the GitHub push was cancelled.")
        return 0
    if push_result != 0:
        rot_say("Signal Rot publish stopped because the GitHub push failed.")
        return push_result

    dry_run_command = _rsync_command(repository, web_root, dry_run=True)

    rot_say("Building a dry-run of the Signal Rot publish...")
    try:
        dry_run = _capture(dry_run_command, repository)
    except FileNotFoundError:
        rot_say("rsync is not installed or is not available in PATH.")
        return 127

    if dry_run.returncode != 0:
        detail = dry_run.stderr.strip() or dry_run.stdout.strip()
        rot_say(f"Signal Rot publish dry-run failed.\n{detail}")
        return dry_run.returncode
    if not dry_run.stdout.strip():
        rot_say("The live Signal Rot website already matches the repository.")
        return 0

    publish_command = _rsync_command(repository, web_root, sudo=True)
    rot_say(
        "SIGNAL ROT PUBLISH PLAN\n"
        "-----------------------\n"
        f"Source:      {repository}\n"
        f"Destination: {web_root}\n"
        "Git data:    excluded\n"
        "Stale files: deleted\n"
        f"Command:     {shlex.join(publish_command)}\n"
        "Changes:\n"
        f"{dry_run.stdout.rstrip()}"
    )

    if review_requested:
        review_prompt = (
            "Review this planned deployment of the Signal Rot repository into "
            "the live Caddy web root. Keep the review read-only and do not "
            "modify files. Inspect the source and destination as needed. Focus "
            "on accidental deletions, missing assets, exposed development or "
            "secret files, broken site behavior, and deployment risks. If the "
            "publish is safe, say so explicitly.\n\n"
            f"Source: {repository}\n"
            f"Destination: {web_root}\n"
            f"rsync dry-run:\n{dry_run.stdout.rstrip()}"
            + (
                f"\n\nAdditional user note:\n{review_note}"
                if review_note
                else ""
            )
        )
        review_result = _review_task(
            review_prompt,
            repository,
            "Rotbot is still reviewing the publish...",
            review_agent
        )
        if review_result != 0:
            return review_result

    if not _confirm("Publish these changes to the live Signal Rot website?"):
        rot_say("Signal Rot publish cancelled. The live website was not changed.")
        return 0

    try:
        publish = subprocess.run(
            publish_command,
            cwd=repository,
            check=False
        )
    except FileNotFoundError:
        rot_say("sudo is not installed or is not available in PATH.")
        return 127

    if publish.returncode != 0:
        rot_say(f"Signal Rot publish failed with exit code {publish.returncode}.")
        return publish.returncode

    rot_say("Signal Rot was published to the live Caddy web root.")
    return 0
