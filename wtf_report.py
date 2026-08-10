import os
from pathlib import Path
import subprocess

from gui import rot_say
from opencode_runner import stream_opencode


SKIPPED_DIRECTORIES = {
    ".git",
    ".mypy_cache",
    ".next",
    ".pytest_cache",
    ".tox",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "target",
    "vendor"
}
PROJECT_FILES = (
    "pyproject.toml",
    "package.json",
    "requirements.txt",
    "setup.py",
    "Cargo.toml",
    "go.mod",
    "composer.json"
)


def _git_output(arguments, directory):
    try:
        result = subprocess.run(
            ["git", "-C", str(directory), *arguments],
            capture_output=True,
            text=True,
            check=False
        )
    except FileNotFoundError:
        return None

    return result


def _project_root(context_directory):
    result = _git_output(["rev-parse", "--show-toplevel"], context_directory)
    if result and result.returncode == 0:
        return Path(result.stdout.strip()).resolve()
    return context_directory.resolve()


def _directory_tree(root, max_depth, max_entries):
    lines = []
    truncated = False

    for current, directories, files in os.walk(root):
        current_path = Path(current)
        relative = current_path.relative_to(root)
        depth = 0 if relative == Path(".") else len(relative.parts)
        directories[:] = sorted(
            name for name in directories
            if name not in SKIPPED_DIRECTORIES
        )

        if depth >= max_depth:
            directories[:] = []

        indent = "  " * depth
        for name in directories:
            lines.append(f"{indent}{name}/")
            if len(lines) >= max_entries:
                truncated = True
                break

        if truncated:
            break

        for name in sorted(files):
            lines.append(f"{indent}{name}")
            if len(lines) >= max_entries:
                truncated = True
                break

        if truncated:
            break

    if truncated:
        lines.append(f"... tree truncated after {max_entries} entries")

    return "\n".join(lines) or "(empty directory)"


def _read_excerpt(path, limit):
    try:
        content = path.read_bytes()
    except OSError as error:
        return f"(could not read: {error})"

    if b"\0" in content[:limit]:
        return "(binary file)"

    truncated = len(content) > limit
    text = content[:limit].decode("utf-8", errors="replace").rstrip()
    if truncated:
        text += f"\n... excerpt truncated after {limit} bytes"
    return text or "(empty file)"


def _important_files(root, target, excerpt_limit):
    candidates = []
    target_file = target.resolve() if target.is_file() else None

    try:
        readmes = sorted(
            path for path in root.iterdir()
            if path.is_file() and path.name.lower().startswith("readme")
        )
    except OSError:
        readmes = []

    candidates.extend(readmes)
    candidates.extend(root / name for name in PROJECT_FILES if (root / name).is_file())

    sections = []
    seen = set()
    for path in candidates:
        resolved = path.resolve()
        if resolved == target_file or resolved in seen:
            continue
        seen.add(resolved)

        try:
            label = resolved.relative_to(root)
        except ValueError:
            label = resolved
        sections.append(
            f"--- {label} ---\n{_read_excerpt(resolved, excerpt_limit)}"
        )

    return "\n\n".join(sections) or "(no README or recognized project files found)"


def _build_snapshot(target, invocation_directory, deep):
    context_directory = target if target.is_dir() else target.parent
    root = _project_root(context_directory)
    tree_depth = 4 if deep else 2
    tree_entries = 400 if deep else 120
    excerpt_limit = 8000 if deep else 3000
    git_status = _git_output(["status", "--short"], root)

    if git_status is None:
        status_text = "(Git is not installed)"
    elif git_status.returncode != 0:
        status_text = "(not a Git repository)"
    else:
        status_text = git_status.stdout.rstrip() or "(clean)"

    return (
        f"MODE: {'DEEP' if deep else 'FAST'}\n"
        f"PWD: {invocation_directory}\n"
        f"TARGET: {target}\n"
        f"PROJECT ROOT: {root}\n\n"
        "GIT STATUS --SHORT\n"
        "------------------\n"
        f"{status_text}\n\n"
        f"DIRECTORY TREE (depth {tree_depth}, dependencies omitted)\n"
        "------------------------------------------------\n"
        f"{_directory_tree(root, tree_depth, tree_entries)}\n\n"
        "README / PROJECT FILE EXCERPTS\n"
        "------------------------------\n"
        f"{_important_files(root, target, excerpt_limit)}"
    ), root


def directory_report(args):
    invocation_directory = Path.cwd().resolve()
    requested_target = getattr(args, "target", None)
    target = (
        Path(requested_target).expanduser().resolve()
        if requested_target
        else invocation_directory
    )

    if not target.exists():
        rot_say(f"Cannot inspect a path that does not exist:\n{target}")
        return 1

    deep = getattr(args, "deep", False)
    note = getattr(args, "note", None)
    snapshot, project_root = _build_snapshot(target, invocation_directory, deep)
    rot_say(
        f"{'DEEP WTF SNAPSHOT' if deep else 'FAST WTF SNAPSHOT'}\n"
        f"{snapshot}"
    )

    if deep:
        prompt = (
            "Produce a deep, read-only WTF report for the requested target. "
            "Use the deterministic snapshot below as a starting point, then "
            "inspect surrounding project context as needed without modifying "
            "files. Cover: what this is, entry points, major files, how it fits "
            "together, architecture, related files, Git state, risks, unfinished "
            "work, testing and gaps, and anything obviously weird. Be specific "
            "and cite paths. Begin with 'DEEP WTF REPORT'.\n\n"
            f"{snapshot}"
            + (
                f"\n\nAdditional user note to address:\n{note}"
                if note
                else ""
            )
        )
        activity = "Rotbot is still digging through the project..."
        mode_name = "Deep"
    else:
        note_instruction = (
            " A narrowly scoped read-only inspection beyond the snapshot is "
            "allowed only when needed to fulfill the additional user note."
            if note
            else ""
        )
        prompt = (
            "Using only the deterministic snapshot below, provide a quick, "
            "read-only orientation. Do not inspect additional files and do not "
            "modify anything. Answer these exact questions with concise, "
            "path-specific evidence: What is this? What is the entry point? "
            "What are the major files? How does it fit together? Anything "
            "obviously weird? Begin with 'FAST WTF REPORT'."
            f"{note_instruction}\n\n"
            f"{snapshot}"
            + (
                f"\n\nAdditional user note to address:\n{note}"
                if note
                else ""
            )
        )
        activity = "Rotbot is still orienting..."
        mode_name = "Fast"

    rot_say(f"Starting {mode_name.lower()} OpenCode orientation...")
    returncode, output, elapsed = stream_opencode(
        prompt,
        activity,
        project_root
    )

    if returncode != 0:
        rot_say(f"WTF inspection failed with exit code {returncode}.")
        return returncode
    if not output.strip():
        rot_say("OpenCode returned an empty WTF report.")
        return 1

    rot_say(f"{mode_name} WTF report finished in {elapsed:.1f}s.")
    return 0
