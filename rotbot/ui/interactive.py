from datetime import datetime

from rotbot.ui.terminal import _terminal_width


def _context_fields(session):
    context = session.context
    user = context.user if context.user is not None else "user: unidentified"
    assistant = (
        context.assistant
        if context.assistant is not None
        else "assistant: unidentified"
    )
    machine = (
        context.machine if context.machine is not None else "machine: unidentified"
    )
    project = f"project: {context.project or 'none'}"
    return user, assistant, machine, project


def _fit(text, width):
    if len(text) <= width:
        return text
    if width <= 3:
        return text[:width]
    return text[:width - 3] + "..."


def render_session_header(session, now=None, width=None):
    now = datetime.now().astimezone() if now is None else now
    width = min(_terminal_width() if width is None else width, 72)
    user, assistant, machine, project = _context_fields(session)
    summary_lines = (f"{user} · {assistant}", f"{machine} · {project}")
    time_text = now.strftime("%I:%M %p").lstrip("0")
    if width < 28:
        return "\n".join(("ROT", *(_fit(line, width) for line in summary_lines), time_text))

    content_width = width - 4
    title = "─ ROT "
    top = "┌" + title + "─" * (width - len(title) - 2) + "┐"

    def row(text):
        return f"│ {_fit(text, content_width):<{content_width}} │"

    return "\n".join((
        top,
        *(row(line) for line in summary_lines),
        row(time_text),
        "└" + "─" * (width - 2) + "┘"
    ))


def render_session_status(session):
    context = session.context
    return "\n".join((
        "ROT SESSION",
        "-----------",
        f"User:       {context.user or 'unidentified'}",
        f"Assistant:  {context.assistant or 'unidentified'}",
        f"Machine:    {context.machine or 'unidentified'}",
        f"Project:    {context.project or 'none'}",
        f"Directory:  {session.cwd}",
        f"Started:    {session.started_at.strftime('%I:%M %p').lstrip('0')}"
    ))


def show_session_header(session):
    print(render_session_header(session))


def clear_terminal():
    print("\033[2J\033[H", end="")
