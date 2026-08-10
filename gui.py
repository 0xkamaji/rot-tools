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
_rotbot_shown = False


def rot_say(message):
    global _rotbot_shown

    bot_width = max(len(line) for line in ROTBOT.splitlines())
    if _rotbot_shown:
        print(f"{'':<{bot_width}}   {ROTBOT_DIVIDER}")

    bot_lines = (
        ROTBOT.splitlines()
        if not _rotbot_shown
        else [f"  {ROTBOT_ARTIFACT}"]
    )
    msg_lines = str(message).splitlines()

    for bot_line, msg_line in zip_longest(
        bot_lines,
        msg_lines,
        fillvalue=""
    ):
        print(f"{bot_line:<{bot_width}}   {msg_line}")

    _rotbot_shown = True


def rot_continue(message):
    bot_width = max(len(line) for line in ROTBOT.splitlines())

    for line in str(message).splitlines():
        print(f"{'':<{bot_width}}   {line}")


def rot_break():
    bot_width = max(len(line) for line in ROTBOT.splitlines())
    print(f"{'':<{bot_width}}   {ROTBOT_DIVIDER}")


def rot_output_start(question=None):
    label = " ROT OUTPUT "
    width = max(get_terminal_size(fallback=(80, 24)).columns, len(label))
    print()
    print(label.center(width, "*"))
    print()

    if question is not None:
        _print_output_text(f"Question: {question}")
        print()
        _print_output_text("Response:")


def _print_output_text(message):
    terminal_width = get_terminal_size(fallback=(80, 24)).columns
    content_width = max(terminal_width - (ROTBOT_OUTPUT_MARGIN * 2), 10)
    margin = " " * ROTBOT_OUTPUT_MARGIN
    logical_lines = str(message).splitlines() or [""]

    for logical_line in logical_lines:
        wrapped_lines = wrap(
            logical_line,
            width=content_width,
            replace_whitespace=False,
            drop_whitespace=False,
            break_long_words=False,
            break_on_hyphens=False
        ) or [""]

        for line in wrapped_lines:
            print(f"{margin}{line.rstrip()}")


def rot_output_line(message):
    _print_output_text(message)


def rot_output_end():
    width = get_terminal_size(fallback=(80, 24)).columns
    print()
    print("*" * width)
    print()


def rot_status(message):
    bot_width = max(len(line) for line in ROTBOT.splitlines())
    status_marker = f"  {ROTBOT_ARTIFACT}"
    message_lines = str(message).splitlines()

    for marker, line in zip_longest(
        [status_marker],
        message_lines,
        fillvalue=""
    ):
        print(f"{marker:<{bot_width}}   {line}")
