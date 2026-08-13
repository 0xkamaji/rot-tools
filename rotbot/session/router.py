import argparse
from dataclasses import dataclass
from functools import lru_cache
import os
import re
import shlex

from rotbot.cli.parser import create_parser
from rotbot.session.shell import available_executables, is_shell_executable


BUILTINS = {
    "help", "status", "history", "pwd", "cd", "clear", "exit", "quit",
    "export", "unset", "talk", "work", "last"
}
CONVERSATIONAL_STARTERS = {
    "why", "what", "how", "who", "where", "when", "can", "could",
    "would", "should", "do", "does", "is", "are", "explain", "tell",
    "this", "that", "these", "those", "maybe", "yeah", "please", "let's",
    "lets"
}
AMBIGUOUS_EXECUTABLES = {
    "find", "time", "sort", "head", "test", "read", "kill"
}
SHELL_OPERATORS = re.compile(r"(?:^|\s)(?:\|\||&&|\||>>|>|<)(?:\s|$)")


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


def _one_edit_away(typed, candidate):
    if typed == candidate or abs(len(typed) - len(candidate)) > 1:
        return False
    if len(typed) == len(candidate):
        differences = [
            index for index, pair in enumerate(zip(typed, candidate))
            if pair[0] != pair[1]
        ]
        if len(differences) == 1:
            return True
        return (
            len(differences) == 2
            and differences[1] == differences[0] + 1
            and typed[differences[0]] == candidate[differences[1]]
            and typed[differences[1]] == candidate[differences[0]]
        )
    shorter, longer = (
        (typed, candidate) if len(typed) < len(candidate) else (candidate, typed)
    )
    index = 0
    while index < len(shorter) and shorter[index] == longer[index]:
        index += 1
    return shorter[index:] == longer[index + 1:]


def _command_typo(first, commands):
    matches = [command for command in commands if _one_edit_away(first, command)]
    if not matches:
        return None
    return min(
        matches,
        key=lambda command: (
            0 if len(command) == len(first) and sorted(command) == sorted(first)
            else 1 if len(command) != len(first)
            else 2,
            command
        )
    )


def _conversational_shape(line, arguments):
    return arguments[0].lower() in CONVERSATIONAL_STARTERS or line.rstrip().endswith("?")


def _path_or_file_shape(argument):
    if argument in {".", ".."} or argument.startswith(("./", "../", "~/", "/")):
        return True
    if "*" in argument or "[" in argument:
        return True
    name = argument.rsplit("/", 1)[-1]
    if "?" in argument and ("/" in argument or "." in name):
        return True
    return "." in name and not name.startswith(".") and not name.endswith(".")


def _strong_shell_shape(line, arguments):
    rest = arguments[1:]
    if not rest:
        return True
    if SHELL_OPERATORS.search(line):
        return True
    if any(argument.startswith("-") for argument in rest):
        return True
    if any(_path_or_file_shape(argument) for argument in rest):
        return True
    if any(
        "=" in argument and argument.split("=", 1)[0].isidentifier()
        for argument in arguments
    ):
        return True
    return False


def _shell_shape(line, arguments):
    return _strong_shell_shape(line, arguments) or (
        len(arguments) > 1 and is_shell_executable(arguments[1])
    )


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

    raw_first = stripped.split(None, 1)[0].lower()
    if raw_first in CONVERSATIONAL_STARTERS or stripped.endswith("?"):
        return Route("ai", stripped)

    try:
        arguments = shlex.split(line)
    except ValueError as error:
        return Route("error", f"Could not parse input: {error}")

    first = arguments[0].lower()
    if first in BUILTINS:
        return Route("builtin", arguments)

    commands = rot_command_names()
    if first in commands:
        return Route("rot", arguments)
    if is_shell_executable(first):
        shell_shaped = _shell_shape(line, arguments)
        if _conversational_shape(line, arguments) and not _strong_shell_shape(
            line, arguments
        ):
            return Route("ai", line.strip())
        if first not in AMBIGUOUS_EXECUTABLES or shell_shaped:
            return Route("shell", line)
        return Route("ai", line.strip())

    if _conversational_shape(line, arguments):
        return Route("ai", line.strip())

    match = _command_typo(first, commands)
    if match is None:
        match = _command_typo(
            first,
            available_executables(os.environ.get("PATH", ""))
        )
    if match:
        suggestion = " ".join((match, *arguments[1:]))
        return Route(
            "error",
            f"Command not found: {first}\nDid you mean `{suggestion}`?"
        )
    return Route("ai", line.strip())
