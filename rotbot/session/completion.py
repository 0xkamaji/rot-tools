import argparse
from dataclasses import dataclass
import os
from pathlib import Path

from rotbot.cli.parser import create_parser
from rotbot.contexts import deletion, entities, loader, machines, people
from rotbot.session.conversations import ConversationStore
from rotbot.session.router import BUILTINS
from rotbot.session.shell import available_executables


@dataclass(frozen=True)
class Completion:
    value: str
    kind: str
    append_space: bool = False


def _partial_tokens(text):
    tokens = []
    token = []
    quote = None
    escaped = False
    for character in text:
        if escaped:
            token.append(character)
            escaped = False
        elif character == "\\" and quote != "'":
            escaped = True
        elif quote is not None:
            if character == quote:
                quote = None
            else:
                token.append(character)
        elif character in {"'", '"'}:
            quote = character
        elif character.isspace():
            if token:
                tokens.append("".join(token))
                token = []
        else:
            token.append(character)
    if escaped:
        token.append("\\")
    trailing_space = bool(text) and text[-1].isspace() and quote is None
    current = "" if trailing_space else "".join(token)
    if token and trailing_space:
        tokens.append("".join(token))
    return tokens, current, quote


def _subparsers(parser):
    return next(
        (
            action for action in parser._actions
            if isinstance(action, argparse._SubParsersAction)
        ),
        None
    )


def _option_map(parser):
    return {
        option: action
        for action in parser._actions
        for option in action.option_strings
    }


class CompletionProvider:
    def __init__(self, session, parser_factory=create_parser):
        self.session = session
        self.parser = parser_factory()

    def complete(self, line_buffer, cursor_index=None):
        before = line_buffer[:cursor_index] if cursor_index is not None else line_buffer
        tokens, current, quote = _partial_tokens(before)
        if before.lstrip().startswith("?"):
            return []
        if before.lstrip().startswith("!"):
            shell_text = before.lstrip()[1:].lstrip()
            tokens, current, quote = _partial_tokens(shell_text)
            if not tokens:
                return self._first_token(current)
            return self._paths(current, quote=quote)
        if not tokens:
            return self._first_token(current)
        first = tokens[0].lower()
        if first == "cd":
            return self._paths(current, directories_only=True, quote=quote)
        if first in BUILTINS:
            if first == "unset":
                return self._values(
                    (name for name in os.environ if name.isidentifier()),
                    current,
                    "environment"
                )
            if first == "export":
                return self._values(
                    (f"{name}=" for name in os.environ if name.isidentifier()),
                    current,
                    "environment"
                )
            return []
        root_subparsers = _subparsers(self.parser)
        if root_subparsers is None or first not in root_subparsers.choices:
            return self._paths(current, quote=quote)
        parser, path, pending = self._parser_state(tokens)
        if parser is None:
            return self._paths(current, quote=quote)
        if pending is not None:
            if pending.choices is not None:
                return self._values(pending.choices, current, "choice")
            return self._dynamic(path, pending.dest, current, quote)
        if current.startswith("-"):
            return self._values(_option_map(parser), current, "rot-option")
        subparsers = _subparsers(parser)
        if subparsers is not None:
            return self._values(subparsers.choices, current, "rot-subcommand")
        positional = self._active_positional(parser, tokens[len(path):])
        if positional is not None:
            if positional.choices is not None:
                return self._values(positional.choices, current, "choice")
            dynamic = self._dynamic(path, positional.dest, current, quote)
            if dynamic:
                return dynamic
        return self._paths(current, quote=quote)

    def _first_token(self, prefix):
        rot_names = set(BUILTINS)
        subparsers = _subparsers(self.parser)
        if subparsers is not None:
            rot_names.update(subparsers.choices)
        matching_rot = sorted(name for name in rot_names if name.startswith(prefix))
        if matching_rot:
            return self._values(matching_rot, prefix, "rot-command")
        executable_names = set(available_executables(os.environ.get("PATH", "")))
        return self._values(executable_names - rot_names, prefix, "executable")

    def _parser_state(self, tokens):
        parser = self.parser
        path = []
        pending = None
        for token in tokens:
            if pending is not None:
                pending = None
                continue
            option = _option_map(parser).get(token)
            if option is not None:
                if option.nargs != 0:
                    pending = option
                continue
            subparsers = _subparsers(parser)
            if subparsers is not None and token in subparsers.choices:
                parser = subparsers.choices[token]
                path.append(token)
        return parser, tuple(path), pending

    def _active_positional(self, parser, consumed_tokens):
        options = _option_map(parser)
        count = 0
        pending = False
        for token in consumed_tokens:
            if pending:
                pending = False
                continue
            action = options.get(token)
            if action is not None:
                pending = action.nargs != 0
                continue
            count += 1
        positionals = [
            action for action in parser._actions
            if not action.option_strings
            and not isinstance(action, argparse._SubParsersAction)
        ]
        return positionals[min(count, len(positionals) - 1)] if positionals else None

    def _dynamic(self, path, destination, prefix, quote):
        try:
            if path == ("context", "show") and destination == "name":
                values = (
                    list(loader.list_contexts())
                    + [item.name for item in entities.list_user_contexts()]
                    + [item.name for item in entities.list_assistant_contexts()]
                    + [item.name for item in machines.list_machine_contexts()]
                    + [
                        item.name for item in people.list_person_contexts()
                        if item.role == "contact"
                    ]
                )
                return self._values(values, prefix, "context")
            if path == ("context", "mod") and destination == "name":
                values = (
                    [item.name for item in entities.list_user_contexts()]
                    + [item.name for item in entities.list_assistant_contexts()]
                    + [
                        item.name for item in people.list_person_contexts()
                        if item.role == "contact"
                    ]
                )
                return self._values(values, prefix, "context")
            if path == ("context", "delete") and destination == "name":
                return self._values(
                    (name for _kind, name in deletion.list_deletable_contexts()),
                    prefix,
                    "context"
                )
            if path == ("ai", "session", "show") and destination == "id":
                return self._values(
                    (item.id for item in ConversationStore().list()),
                    prefix,
                    "conversation"
                )
            if path == ("context", "bind"):
                return self._paths(prefix, directories_only=True, quote=quote)
        except Exception:
            return []
        return []

    def _values(self, values, prefix, kind):
        candidates = sorted({str(value) for value in values if str(value).startswith(prefix)})
        return [Completion(value + " ", kind, True) for value in candidates]

    def _paths(self, prefix, directories_only=False, quote=None):
        expanded = os.path.expanduser(prefix)
        directory_part, name_prefix = os.path.split(expanded)
        lookup = Path(directory_part or ".")
        if not lookup.is_absolute():
            lookup = self.session.cwd / lookup
        lexical_directory = prefix[:len(prefix) - len(os.path.basename(prefix))]
        try:
            entries = tuple(os.scandir(lookup))
        except OSError:
            return []
        completions = []
        for entry in entries:
            try:
                is_directory = entry.is_dir()
                if directories_only and not is_directory:
                    continue
            except OSError:
                continue
            if not entry.name.startswith(name_prefix):
                continue
            if entry.name.startswith(".") and not name_prefix.startswith("."):
                continue
            value = lexical_directory + entry.name
            if quote is None:
                value = value.replace(" ", "\\ ")
            else:
                value = quote + value
            if is_directory:
                value += os.sep
            elif quote is not None:
                value += quote + " "
            completions.append(Completion(value, "directory" if is_directory else "path"))
        return sorted(completions, key=lambda item: (item.kind != "directory", item.value))
