import argparse
from dataclasses import dataclass
from difflib import get_close_matches
from functools import lru_cache
import shlex

from rotbot.cli.parser import create_parser
from rotbot.session.shell import is_shell_command


@dataclass(frozen=True)
class Route:
    kind: str
    value: object


@lru_cache(maxsize=1)
def rot_command_names():
    parser = create_parser()
    subparsers = next(
        action
        for action in parser._actions
        if isinstance(action, argparse._SubParsersAction)
    )
    return tuple(subparsers.choices)


def _command_typo(first, commands):
    for command in commands:
        if len(first) != len(command):
            continue
        differences = [
            index for index, pair in enumerate(zip(first, command))
            if pair[0] != pair[1]
        ]
        if (
            len(differences) == 2
            and differences[1] == differences[0] + 1
            and first[differences[0]] == command[differences[1]]
            and first[differences[1]] == command[differences[0]]
        ):
            return command
    match = get_close_matches(first, commands, n=1, cutoff=0.78)
    return match[0] if match else None


def route_input(line):
    stripped = line.strip()
    if not stripped:
        return Route("empty", None)
    if stripped.startswith("?"):
        message = stripped[1:].lstrip()
        return Route("ai", message) if message else Route("error", "Usage: ? MESSAGE")
    if stripped.startswith("!"):
        command = stripped[1:].lstrip()
        return Route("shell", command) if command else Route("error", "Usage: ! COMMAND")

    try:
        arguments = shlex.split(line)
    except ValueError as error:
        return Route("error", f"Could not parse input: {error}")

    first = arguments[0].lower()
    if first in {"help", "status", "history", "pwd", "cd", "clear", "exit", "quit", "export", "unset"}:
        return Route("builtin", arguments)

    commands = rot_command_names()
    if first in commands:
        return Route("rot", arguments)
    if is_shell_command(arguments):
        return Route("shell", line)

    match = _command_typo(first, commands)
    if match:
        suggestion = " ".join((match, *arguments[1:]))
        return Route("error", f"Unknown command: {first}\nDid you mean: {suggestion}?")
    return Route("ai", line.strip())
