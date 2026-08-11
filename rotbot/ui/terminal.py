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
ROTBOT_MIN_CONTENT_WIDTH = 35
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


def rot_content_width():
    terminal_width, _bot_width, prefix_width = _bot_layout()
    if terminal_width < prefix_width + ROTBOT_MIN_CONTENT_WIDTH:
        return terminal_width
    return terminal_width - prefix_width


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


def rot_table(headers, rows):
    terminal_width, bot_width, prefix_width = _bot_layout()
    compact = terminal_width < prefix_width + ROTBOT_MIN_CONTENT_WIDTH
    width = terminal_width if compact else terminal_width - prefix_width
    prefix = "" if compact else f"{'':<{bot_width}}{' ' * ROTBOT_GAP}"
    columns = len(headers)
    overhead = (columns * 3) + 1
    cell_space = width - overhead

    if columns == 0:
        return

    values = [tuple(str(value) for value in row) for row in rows]
    if cell_space < columns:
        for row in values:
            rot_continue(" - ".join(row))
        return

    desired = [
        max([len(str(header))] + [len(row[index]) for row in values])
        for index, header in enumerate(headers)
    ]
    column_widths = [1] * columns
    while sum(column_widths) < cell_space:
        candidates = [
            index
            for index in range(columns)
            if column_widths[index] < desired[index]
        ]
        if not candidates:
            column_widths[-1] += cell_space - sum(column_widths)
            break
        index = min(candidates, key=lambda item: column_widths[item])
        column_widths[index] += 1

    def print_row(row):
        wrapped_cells = [
            _wrapped_lines(value, column_widths[index])
            for index, value in enumerate(row)
        ]
        height = max(len(cell) for cell in wrapped_cells)
        for line_number in range(height):
            cells = [
                cell[line_number] if line_number < len(cell) else ""
                for cell in wrapped_cells
            ]
            rendered = "| " + " | ".join(
                cell.ljust(column_widths[index])
                for index, cell in enumerate(cells)
            ) + " |"
            print(f"{prefix}{rendered}")

    separator = "|-" + "-|-".join("-" * size for size in column_widths) + "-|"
    print(f"{prefix}{separator}")
    print_row(tuple(str(header) for header in headers))
    print(f"{prefix}{separator}")
    for row in values:
        print_row(row)
        print(f"{prefix}{separator}")


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
