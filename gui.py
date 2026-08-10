from itertools import zip_longest


ROTBOT = r"""
   .-.
  [x_o]
  /|%|\
   / \
  ROTBOT
""".strip("\n")
_rotbot_shown = False


def rot_say(message):
    global _rotbot_shown

    bot_lines = ROTBOT.splitlines() if not _rotbot_shown else []
    msg_lines = str(message).splitlines()
    bot_width = max(len(line) for line in ROTBOT.splitlines())

    for bot_line, msg_line in zip_longest(
        bot_lines,
        msg_lines,
        fillvalue=""
    ):
        print(f"{bot_line:<{bot_width}}   {msg_line}")

    _rotbot_shown = True
