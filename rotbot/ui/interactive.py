from datetime import datetime
import os
from shutil import get_terminal_size
import sys

from rotbot.ui.terminal import ROTBOT_ARTIFACT, ROTBOT_BODY, _terminal_width
from rotbot.ui.ai import ThinkingSpinner, _output_lock




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
    details = (
        f"{user} · {assistant}",
        f"{machine} · {project}",
        f"cwd: {session.cwd}",
        f"{session.authority_mode} · AI: {session.ai_status} · "
        f"{now.strftime('%I:%M %p').lstrip('0')}"
    )
    if width < 28:
        return "\n".join((
            ROTBOT_BODY,
            *(_fit(line, width) for line in details)
        ))

    content_width = width - 4
    title = "─ ROT "
    top = "┌" + title + "─" * (width - len(title) - 2) + "┐"
    art = ROTBOT_BODY.splitlines()
    art_width = max(len(line) for line in art)
    detail_width = max(content_width - art_width - 2, 1)

    def row(art_line, detail):
        body = f"{art_line:<{art_width}}  {_fit(detail, detail_width):<{detail_width}}"
        return f"│ {body:<{content_width}} │"

    return "\n".join((
        top,
        *(
            row(
                art[index] if index < len(art) else "",
                details[index] if index < len(details) else ""
            )
            for index in range(max(len(art), len(details)))
        ),
        "└" + "─" * (width - 2) + "┘"
    ))


def render_session_status(session):
    context = session.context
    lines = [
        "ROT SESSION",
        "-----------",
        f"User:       {context.user or 'unidentified'}",
        f"Assistant:  {context.assistant or 'unidentified'}",
        f"Machine:    {context.machine or 'unidentified'}",
        f"Project:    {context.project or 'none'}",
        f"Directory:  {session.cwd}",
        f"Started:    {session.started_at.strftime('%I:%M %p').lstrip('0')}",
        "",
        f"Mode:       {session.authority_mode}",
        f"AI:         {session.ai_status}"
    ]
    if session.ai_status == "active":
        lines.append(f"Backend:    {session.ai.backend.name}")
    return "\n".join(lines)


def interactive_prompt(session, stream=None):
    stream = sys.stdout if stream is None else stream
    user = (session.context.user or "user").strip().lower()
    marker = "❯" if getattr(stream, "isatty", lambda: False)() else ">"
    return f"{user} {marker} "


def render_rot_response(session, message):
    assistant = (session.context.assistant or "rot").strip().lower()
    print(f"\n{assistant} {ROTBOT_ARTIFACT}\n{message.rstrip()}\n")


class StreamingRotResponse:
    def __init__(self, session, stream=None, spinner=None):
        self.session = session
        self.stream = sys.stdout if stream is None else stream
        assistant = (session.context.assistant or "rot").strip().lower()
        self.spinner = spinner or ThinkingSpinner(assistant, self.stream)
        self.started = False
        self.finished = False

    def start(self):
        self.spinner.start()

    def write(self, text):
        if not text:
            return
        first = not self.started
        if first:
            self.spinner.stop()
        with _output_lock:
            if first:
                assistant = (self.session.context.assistant or "rot").strip().lower()
                self.stream.write(f"\n{assistant} {ROTBOT_ARTIFACT}\n")
                self.started = True
            self.stream.write(text)
            self.stream.flush()

    def finish(self, interrupted=False):
        if self.finished:
            return
        self.finished = True
        self.spinner.stop(clear=True)
        with _output_lock:
            if self.started:
                self.stream.write("\n\n" if not interrupted else "\n\n^C\n\n")
                self.stream.flush()


def show_session_header(session):
    print(render_session_header(session))


def clear_terminal():
    print("\033[2J\033[H", end="")


class SessionHeader:
    def __init__(self, stream=None):
        self.stream = sys.stdout if stream is None else stream
        self.fixed = False
        self.size = None
        self.height = 0

    def _terminal_size(self):
        size = get_terminal_size(fallback=(80, 24))
        return size.columns, size.lines

    def _supports_fixed_header(self, height, rows):
        return (
            getattr(self.stream, "isatty", lambda: False)()
            and os.environ.get("TERM", "").lower() not in {"", "dumb"}
            and rows >= height + 3
        )

    def _write(self, content):
        self.stream.write(content)
        self.stream.flush()

    def _establish(self, session, columns, rows):
        rendered = render_session_header(session, width=columns)
        lines = rendered.splitlines()
        height = len(lines)
        if not self._supports_fixed_header(height, rows):
            if self.fixed:
                self._write("\033[r\033[2J\033[H")
            self.fixed = False
            self.size = (columns, rows)
            self.height = height
            self._write(rendered + "\n")
            return

        header = "".join(
            f"\033[{row};1H\033[2K{line}"
            for row, line in enumerate(lines, 1)
        )
        self.fixed = True
        self.size = (columns, rows)
        self.height = height
        self._write(
            "\033[r\033[2J\033[H"
            + header
            + f"\033[{height + 1};{rows}r"
            + f"\033[{height + 1};1H"
        )

    def start(self, session):
        self._establish(session, *self._terminal_size())

    def refresh(self, session):
        columns, rows = self._terminal_size()
        if not self.fixed:
            return
        if self.size != (columns, rows):
            self._establish(session, columns, rows)
            return

        lines = render_session_header(session, width=columns).splitlines()
        if len(lines) != self.height:
            self._establish(session, columns, rows)
            return
        header = "".join(
            f"\033[{row};1H\033[2K{line}"
            for row, line in enumerate(lines, 1)
        )
        self._write(
            "\0337"
            + f"\033[{self.height + 1};{rows}r"
            + header
            + "\0338"
        )

    def clear(self, session):
        if not self.fixed:
            clear_terminal()
            return
        self._establish(session, *self._terminal_size())

    def stop(self):
        if self.fixed:
            self._write("\033[r\033[999;1H")
        self.fixed = False
