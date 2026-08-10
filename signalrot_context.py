from datetime import date
from pathlib import Path
import re

from agents.runner import stream_agent
from gui import rot_continue, rot_say


CONTEXT_ROOT = Path(__file__).resolve().parent / "context" / "signalrot"
IDENTITY_PATH = CONTEXT_ROOT / "signalrot_identity.md"
REFRESH_PATH = CONTEXT_ROOT / "signalrot_refresh.md"
TRACKED_SECTIONS = ("OPPSEC", "Hacks", "Signals", "Beats", "Frames", "Contact")


def _read_context_file(path):
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _section(markdown, heading):
    lines = markdown.splitlines()
    heading_pattern = re.compile(rf"^#+\s+{re.escape(heading)}\s*$", re.IGNORECASE)
    start = next(
        (index + 1 for index, line in enumerate(lines) if heading_pattern.match(line)),
        None
    )
    if start is None:
        return ""

    section_lines = []
    for line in lines[start:]:
        if line.startswith("#"):
            break
        section_lines.append(line)
    return "\n".join(section_lines).strip()


def _list_items(markdown, heading):
    return [
        line[2:].strip()
        for line in _section(markdown, heading).splitlines()
        if line.startswith("- ")
    ]


def _identity_sections(identity):
    lines = identity.splitlines()
    start = next(
        (
            index + 1
            for index, line in enumerate(lines)
            if line.strip() == "# Content Philosophy"
        ),
        None
    )
    sections = []
    if start is not None:
        for line in lines[start:]:
            if line.startswith("# "):
                break
            if line.startswith("## "):
                sections.append(line[3:].strip())

    if "Contact" not in sections:
        sections.append("Contact")
    return sections


def _identity_summary(identity):
    identity_text = " ".join(_section(identity, "What SignalRot Is").split())
    match = re.search(r"SignalRot is my (.+?), and home", identity_text)
    if not match:
        return ["personal website / digital garden /", "technical notebook / creative archive"]

    parts = [part.strip() for part in match.group(1).split(",")]
    midpoint = max(1, len(parts) // 2)
    return [
        " / ".join(parts[:midpoint]) + " /",
        " / ".join(parts[midpoint:])
    ]


def _section_updates(refresh):
    lines = refresh.splitlines()
    start = next(
        (
            index + 1
            for index, line in enumerate(lines)
            if line.strip() == "## Section updates"
        ),
        None
    )
    if start is None:
        return []

    updates = []
    section_name = None
    fields = {}

    for line in lines[start:]:
        if line.startswith("## "):
            break
        if line.startswith("### "):
            if section_name:
                updates.append((section_name, fields))
            section_name = line[4:].strip()
            fields = {}
        elif section_name and line.startswith("- ") and ":" in line:
            key, value = line[2:].split(":", 1)
            fields[key.strip()] = value.strip()

    if section_name:
        updates.append((section_name, fields))
    return updates


def signalrot_context_block():
    identity = _read_context_file(IDENTITY_PATH)
    refresh = _read_context_file(REFRESH_PATH)
    return (
        "SIGNALROT IDENTITY (STATIC; NEVER REWRITE)\n"
        "------------------------------------------\n"
        f"{identity or '(identity context unavailable)'}\n\n"
        "SIGNALROT CURRENT STATE (REFRESHABLE)\n"
        "-------------------------------------\n"
        f"{refresh or '(current state unavailable)'}"
    )


def with_signalrot_context(prompt):
    return f"{prompt}\n\n{signalrot_context_block()}"


def _summary_text():
    identity = _read_context_file(IDENTITY_PATH)
    refresh = _read_context_file(REFRESH_PATH)
    refreshed = re.search(r"^Last refreshed:\s*(.+)$", refresh, re.MULTILINE)
    sections = _list_items(refresh, "Current sections") or _identity_sections(identity)
    published = _list_items(refresh, "Published content")
    section_updates = _section_updates(refresh)

    lines = [
        "Identity:",
        "context/signalrot/signalrot_identity.md",
        "[loaded]" if identity else "[missing]",
        "",
        "Current state:",
        "context/signalrot/signalrot_refresh.md",
        "[loaded]" if refresh else "[missing]",
        "",
        "State last refreshed:",
        refreshed.group(1).strip() if refreshed else "never",
        "",
        "signalrot:"
    ]
    lines.extend(_identity_summary(identity))
    lines.extend(("", "Current sections:"))
    lines.extend(sections or ["(unknown)"])
    lines.extend(("", "Published:"))
    lines.extend(published or ["(run rot sr context --refresh)"])
    lines.extend(("", "Section updates:"))
    if section_updates:
        for section_name, fields in section_updates:
            lines.extend((
                section_name,
                f"  Last: {fields.get('Latest addition or change', 'unknown')}",
                f"  Updated: {fields.get('Last changed', 'unknown')}"
            ))
    else:
        lines.append("(run rot sr context --refresh)")
    return "\n".join(lines)


def show_signalrot_context():
    rot_say("SIGNALROT CONTEXT")
    rot_continue(_summary_text())
    return 0


def _clean_refresh_output(output):
    content = output.strip()
    if content.startswith("```"):
        lines = content.splitlines()[1:]
        if lines and lines[-1].strip() == "```":
            lines.pop()
        content = "\n".join(lines).strip()

    heading = content.find("# SignalRot Current State")
    return content[heading:] if heading >= 0 else content


def refresh_signalrot_context(
    args,
    repository,
    web_root,
    git_status,
    deployment_diff
):
    refreshed_on = date.today().isoformat()
    prompt = with_signalrot_context(
        "Regenerate the refreshable signalrot current-state document by "
        "inspecting the repository and the supplied production comparison. "
        "Do not modify any files. Treat the identity context as immutable. "
        "Return only the complete Markdown document, without a code fence or "
        "commentary. Describe what currently exists, summarize newest content "
        "and additions since the previous refresh, count published content by "
        "section where possible, identify current focus, compare repository and "
        "production, and suggest concrete next steps. Use exactly this structure:\n\n"
        "# SignalRot Current State\n\n"
        f"Last refreshed: {refreshed_on}\n\n"
        "# Snapshot\n\n"
        "## Overview\n\n"
        "## Current sections\n\n"
        "Use one bullet per section containing only the section name.\n\n"
        "## Published content\n\n"
        "Use concise count bullets such as `7 OPPSEC guides`.\n\n"
        "## Section updates\n\n"
        "Create each subsection below. Inspect repository content and Git "
        "history to identify the most recent addition or meaningful change and "
        "its date. Use `unknown` only when history cannot establish a date. "
        "Beats and Frames currently lead to external links; state that as their "
        "current behavior, but still report their latest link or configuration "
        "change so future additions are tracked. Use exactly these fields:\n\n"
        "### OPPSEC\n"
        "- Current: ...\n"
        "- Latest addition or change: ...\n"
        "- Last changed: YYYY-MM-DD or unknown\n\n"
        "### Hacks\n"
        "- Current: ...\n"
        "- Latest addition or change: ...\n"
        "- Last changed: YYYY-MM-DD or unknown\n\n"
        "### Signals\n"
        "- Current: ...\n"
        "- Latest addition or change: ...\n"
        "- Last changed: YYYY-MM-DD or unknown\n\n"
        "### Beats\n"
        "- Current: external link section\n"
        "- Latest addition or change: ...\n"
        "- Last changed: YYYY-MM-DD or unknown\n\n"
        "### Frames\n"
        "- Current: external link section\n"
        "- Latest addition or change: ...\n"
        "- Last changed: YYYY-MM-DD or unknown\n\n"
        "### Contact\n"
        "- Current: ...\n"
        "- Latest addition or change: ...\n"
        "- Last changed: YYYY-MM-DD or unknown\n\n"
        "## New since previous refresh\n\n"
        "## Current focus\n\n"
        "## Repository vs production\n\n"
        "## Possible next steps\n\n"
        f"Repository: {repository}\n"
        f"Production web root: {web_root}\n\n"
        f"Git status --short:\n{git_status or '(clean)'}\n\n"
        f"Deployment dry-run:\n{deployment_diff or '(no deployment changes)'}"
        + (
            f"\n\nAdditional user note:\n{args.note}"
            if getattr(args, "note", None)
            else ""
        )
    )

    rot_say("Refreshing signalrot current-state context...")
    returncode, output, elapsed = stream_agent(
        prompt,
        "Rotbot is still refreshing signalrot context...",
        repository,
        agent_name=getattr(args, "agent", None)
    )
    if returncode != 0:
        rot_say(f"signalrot context refresh failed with exit code {returncode}.")
        return returncode

    content = _clean_refresh_output(output)
    if (
        not content.startswith("# SignalRot Current State")
        or f"Last refreshed: {refreshed_on}" not in content
        or any(f"### {section}" not in content for section in TRACKED_SECTIONS)
    ):
        rot_say("The AI agent returned an invalid signalrot context document.")
        return 1

    try:
        temporary_path = REFRESH_PATH.with_suffix(".tmp")
        temporary_path.write_text(content.rstrip() + "\n", encoding="utf-8")
        temporary_path.replace(REFRESH_PATH)
    except OSError as error:
        rot_say(f"Could not write signalrot refresh context.\n{error}")
        return 1

    rot_say(f"signalrot context refreshed in {elapsed:.1f}s.")
    return show_signalrot_context()
