import re
from textwrap import shorten

from rotbot.contexts.loader import (
    Context,
    ContextError,
    load_context
)
from rotbot.ui.terminal import rot_continue, rot_say, rot_table


def _load_signalrot_context():
    try:
        return load_context("signalrot")
    except ContextError:
        return Context("signalrot", "", "")


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


def _summary_text():
    context = _load_signalrot_context()
    identity = context.identity
    refresh = context.state
    refreshed = re.search(r"^Last refreshed:\s*(.+)$", refresh, re.MULTILINE)
    sections = _list_items(refresh, "Current sections") or _identity_sections(identity)
    published = _list_items(refresh, "Published content")
    identity_summary = " ".join(_identity_summary(identity))
    lines = [
        "Identity: identity.md "
        f"[{'loaded' if identity else 'missing'}]",
        "State: state.md "
        f"[{'loaded' if refresh else 'missing'}]",
        f"Refreshed: {refreshed.group(1).strip() if refreshed else 'never'}",
        "",
        f"SignalRot: {identity_summary}",
        f"Sections: {', '.join(sections) if sections else '(unknown)'}",
        "Published: " + (
            ", ".join(published)
            if published
            else "none recorded"
        ),
        "",
        "Updates:"
    ]
    return "\n".join(lines)


def _updates_rows():
    refresh = _load_signalrot_context().state
    rows = []
    for section_name, fields in _section_updates(refresh):
        rows.append((
            section_name,
            fields.get("Last changed", "unknown"),
            shorten(
                fields.get("Latest addition or change", "unknown"),
                width=64,
                placeholder="..."
            )
        ))
    return rows


def show_signalrot_context():
    rot_say("SIGNALROT CONTEXT")
    rot_continue(_summary_text())
    rows = _updates_rows()
    if rows:
        rot_table(("Section", "Last update", "Update information"), rows)
    else:
        rot_continue("No section updates recorded.")
    return 0
