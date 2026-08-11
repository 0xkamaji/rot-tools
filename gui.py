from itertools import zip_longest
from shutil import get_terminal_size
from textwrap import wrap


ROTBOT = r"""
   .-.
  [x_o]
  /|%|\
   / \
  ROTBOT
""".strip("\n")
ROTBOT_ARTIFACT = "[x_o]"
ROTBOT_DIVIDER = "----------[ rot ]----------"
ROTBOT_OUTPUT_MARGIN = 4
ROTBOT_GAP = 3
ROTBOT_MIN_CONTENT_WIDTH = 20
_rotbot_shown = False


def _terminal_width():
    return max(get_terminal_size(fallback=(80, 24)).columns, 1)


def _wrapped_lines(message, width):
    lines = []
    for logical_line in str(message).splitlines() or [""]:
        if not logical_line:
            lines.append("")
            continue

        indent = logical_line[:len(logical_line) - len(logical_line.lstrip())]
        indent = indent[:max(width - 1, 0)]
        text = logical_line.lstrip()
        lines.extend(wrap(
            text,
            width=width,
            initial_indent=indent,
            subsequent_indent=indent,
            replace_whitespace=False,
            drop_whitespace=True,
            break_long_words=True,
            break_on_hyphens=False
        ) or [indent])
    return lines


def _bot_layout():
    terminal_width = _terminal_width()
    bot_width = max(len(line) for line in ROTBOT.splitlines())
    prefix_width = bot_width + ROTBOT_GAP
    return terminal_width, bot_width, prefix_width


def _divider(width):
    if width >= len(ROTBOT_DIVIDER):
        return ROTBOT_DIVIDER
    label = "[ rot ]"
    return label.center(width, "-") if width >= len(label) else "-" * width


def rot_say(message):
    global _rotbot_shown

    terminal_width, bot_width, prefix_width = _bot_layout()
    if terminal_width < prefix_width + ROTBOT_MIN_CONTENT_WIDTH:
        if _rotbot_shown:
            print(_divider(terminal_width))
        print(ROTBOT_ARTIFACT[:terminal_width])
        for line in _wrapped_lines(message, terminal_width):
            print(line)
        _rotbot_shown = True
        return

    content_width = terminal_width - prefix_width
    if _rotbot_shown:
        print(f"{'':<{bot_width}}{' ' * ROTBOT_GAP}{_divider(content_width)}")

    bot_lines = (
        ROTBOT.splitlines()
        if not _rotbot_shown
        else [f"  {ROTBOT_ARTIFACT}"]
    )
    msg_lines = _wrapped_lines(message, content_width)

    for bot_line, msg_line in zip_longest(
        bot_lines,
        msg_lines,
        fillvalue=""
    ):
        print(f"{bot_line:<{bot_width}}{' ' * ROTBOT_GAP}{msg_line}")

    _rotbot_shown = True


def rot_continue(message):
    terminal_width, bot_width, prefix_width = _bot_layout()
    if terminal_width < prefix_width + ROTBOT_MIN_CONTENT_WIDTH:
        for line in _wrapped_lines(message, terminal_width):
            print(line)
        return

    for line in _wrapped_lines(message, terminal_width - prefix_width):
        print(f"{'':<{bot_width}}{' ' * ROTBOT_GAP}{line}")


def rot_break():
    terminal_width, bot_width, prefix_width = _bot_layout()
    if terminal_width < prefix_width + ROTBOT_MIN_CONTENT_WIDTH:
        print(_divider(terminal_width))
        return
    print(
        f"{'':<{bot_width}}{' ' * ROTBOT_GAP}"
        f"{_divider(terminal_width - prefix_width)}"
    )


def rot_output_start(question=None):
    label = " ROT OUTPUT "
    width = _terminal_width()
    print()
    print(label.center(width, "*") if width >= len(label) else "*" * width)
    print()

    if question is not None:
        _print_output_text(f"Question: {question}")
        print()
        _print_output_text("Response:")


def _print_output_text(message):
    terminal_width = _terminal_width()
    margin_width = min(ROTBOT_OUTPUT_MARGIN, max((terminal_width - 1) // 2, 0))
    content_width = max(terminal_width - (margin_width * 2), 1)
    margin = " " * margin_width

    for line in _wrapped_lines(message, content_width):
        print(f"{margin}{line}")


def rot_output_line(message):
    _print_output_text(message)


def rot_output_end():
    width = _terminal_width()
    print()
    print("*" * width)
    print()


def rot_status(message):
    terminal_width, bot_width, prefix_width = _bot_layout()
    if terminal_width < prefix_width + ROTBOT_MIN_CONTENT_WIDTH:
        lines = _wrapped_lines(message, terminal_width)
        print(ROTBOT_ARTIFACT[:terminal_width])
        for line in lines:
            print(line)
        return

    status_marker = f"  {ROTBOT_ARTIFACT}"
    message_lines = _wrapped_lines(message, terminal_width - prefix_width)

    for marker, line in zip_longest(
        [status_marker],
        message_lines,
        fillvalue=""
    ):
        print(f"{marker:<{bot_width}}{' ' * ROTBOT_GAP}{line}")
