from itertools import zip_longest


ROTBOT = r"""
   .-.
  [x_o]
  /|%|\
   / \
  ROTBOT
""".strip("\n")
ROTBOT_ARTIFACT = "[x_o]"
ROTBOT_DIVIDER = "----------[ rot ]----------"
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
