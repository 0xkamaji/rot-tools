import argparse

from rotbot.agents.config import AGENT_CHOICES
from rotbot.agents.runner import ask_agent
from rotbot.commands.git import git_pull, git_push, git_status
from rotbot.commands.machine import machine_inspect
from rotbot.commands.wtf import directory_report
from rotbot.contexts.binding import context_bind
from rotbot.contexts.creation import context_add
from rotbot.contexts.deletion import context_delete
from rotbot.contexts.inspection import context_inspect
from rotbot.contexts.loader import context_list, context_show
from rotbot.contexts.menu import context_menu
from rotbot.contexts.modification import context_mod
from rotbot.integrations.signalrot.commands import (
    sr_context,
    sr_diff,
    sr_publish,
    sr_pull,
    sr_push,
    sr_status
)
from rotbot.ui.terminal import rot_content_width, rot_say


class VerboseHelpAction(argparse.Action):
    def __init__(self, option_strings, dest=argparse.SUPPRESS, **kwargs):
        super().__init__(option_strings, dest, nargs=0, **kwargs)

    def __call__(self, parser, namespace, values, option_string=None):
        parser.print_verbose_help()
        parser.exit()


class RotArgumentParser(argparse.ArgumentParser):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.add_argument(
            "-hv",
            "--help-verbose",
            action=VerboseHelpAction,
            help="Show detailed help for this command and all subcommands"
        )

    def _get_formatter(self):
        return self.formatter_class(
            prog=self.prog,
            width=rot_content_width()
        )

    def print_help(self, file=None):
        rot_say(self.format_help().rstrip())

    def print_verbose_help(self):
        sections = ["ROTBOT VERBOSE HELP", self.format_help().rstrip()]
        help_options = {"-h", "--help", "-hv", "--help-verbose"}

        def append_subcommands(current, depth=0):
            for action in current._actions:
                if not isinstance(action, argparse._SubParsersAction):
                    continue
                for subparser in action.choices.values():
                    hidden_actions = [
                        item for item in subparser._actions
                        if help_options.intersection(item.option_strings)
                    ]
                    original_help = [item.help for item in hidden_actions]
                    for item in hidden_actions:
                        item.help = argparse.SUPPRESS
                    try:
                        command_help = subparser.format_help().rstrip()
                    finally:
                        for item, help_text in zip(hidden_actions, original_help):
                            item.help = help_text
                    if depth == 0:
                        divider = "=" * 60
                        sections.append(
                            f"{divider}\nCOMMAND: {subparser.prog}\n{divider}"
                        )
                    else:
                        sections.append(f"COMMAND: {subparser.prog}")
                    sections.append(command_help)
                    append_subcommands(subparser, depth + 1)

        append_subcommands(self)

        rot_say("\n\n".join(sections))

    def error(self, message):
        rot_say(f"{self.format_usage().strip()}\n\nError: {message}")
        self.exit(2)


def _add_note_argument(command_parser):
    command_parser.add_argument(
        "-n",
        "--note",
        help="Add a user caveat or request to the AI prompt"
    )


def _add_agent_argument(command_parser):
    command_parser.add_argument(
        "-a",
        "--agent",
        choices=AGENT_CHOICES,
        help="Choose the AI agent for this command"
    )


def _add_git_push_arguments(command_parser):
    command_parser.add_argument(
        "--review",
        action="store_true",
        help="Ask the AI agent to review changes before committing"
    )
    command_parser.add_argument(
        "-m",
        "--message",
        help="Use this commit message instead of prompting"
    )
    _add_agent_argument(command_parser)
    _add_note_argument(command_parser)


def create_parser():
    parser = RotArgumentParser(
        prog="rotbot",
        description="Launch Rotbot using either the 'rotbot' or 'rot' command.",
        epilog=(
            "Examples:\n"
            "  rotbot sr status\n"
            "  rot sr status\n"
            "  rot pull\n"
            "  rot push\n"
            "  rot push --review\n"
            "  rot git pull\n"
            "  rot git push --review\n"
            "  rot git status\n"
            "  rot wtf\n"
            "  rot wtf -n \"also count occurrences of chicken\"\n"
            "  rot wtf path/to/file.py\n"
            "  rot wtf --deep path/to/directory\n"
            "  rotbot ask \"What is today's date?\"\n"
            "  rot ask \"What is today's date?\""
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    commands = parser.add_subparsers(dest="command", required=True)

    ask_parser = commands.add_parser(
        "ask",
        help="Ask the configured AI agent a question"
    )
    ask_parser.add_argument(
        "question",
        nargs="+",
        help="Question to send to the AI agent"
    )
    _add_agent_argument(ask_parser)
    ask_parser.set_defaults(func=ask_agent)

    pull_parser = commands.add_parser(
        "pull",
        help="Pull the current Git repository"
    )
    pull_parser.set_defaults(func=git_pull)

    push_parser = commands.add_parser(
        "push",
        help="Stage, commit, and push the current Git repository"
    )
    _add_git_push_arguments(push_parser)
    push_parser.set_defaults(func=git_push)

    git_parser = commands.add_parser(
        "git",
        help="Git repository commands"
    )
    git_commands = git_parser.add_subparsers(
        dest="git_command",
        required=True
    )

    git_pull_parser = git_commands.add_parser(
        "pull",
        help="Pull the current Git repository"
    )
    git_pull_parser.set_defaults(func=git_pull)

    git_push_parser = git_commands.add_parser(
        "push",
        help="Stage, commit, and push the current Git repository"
    )
    _add_git_push_arguments(git_push_parser)
    git_push_parser.set_defaults(func=git_push)

    git_status_parser = git_commands.add_parser(
        "status",
        help="Summarize the current Git repository"
    )
    git_status_parser.add_argument(
        "--fetch",
        action="store_true",
        help="Fetch the configured upstream remote before comparing"
    )
    git_status_parser.set_defaults(func=git_status)

    wtf_parser = commands.add_parser(
        "wtf",
        help="Explain a file or directory and what it does"
    )
    wtf_parser.add_argument(
        "target",
        nargs="?",
        help="Optional file or directory to inspect"
    )
    wtf_parser.add_argument(
        "--deep",
        action="store_true",
        help="Inspect broader context, architecture, risks, and testing"
    )
    _add_agent_argument(wtf_parser)
    _add_note_argument(wtf_parser)
    wtf_parser.set_defaults(func=directory_report)

    machine_parser = commands.add_parser(
        "machine",
        help="Inspect local machine information"
    )
    machine_commands = machine_parser.add_subparsers(
        dest="machine_command",
        required=True
    )
    machine_inspect_parser = machine_commands.add_parser(
        "inspect",
        help="Inspect this machine without writing files"
    )
    machine_inspect_parser.set_defaults(func=machine_inspect)

    context_parser = commands.add_parser(
        "context",
        help="Inspect, list, show, add, modify, bind, or archive contexts"
    )
    context_commands = context_parser.add_subparsers(
        dest="context_command"
    )
    context_parser.set_defaults(func=context_menu, context_command=None)

    context_list_parser = context_commands.add_parser(
        "list",
        help="List available contexts"
    )
    context_list_parser.set_defaults(func=context_list)

    context_inspect_parser = context_commands.add_parser(
        "inspect",
        help="Inspect the current RotBot context without writing files"
    )
    context_inspect_parser.set_defaults(func=context_inspect)

    context_show_parser = context_commands.add_parser(
        "show",
        help="Show a context"
    )
    context_show_parser.add_argument(
        "name",
        nargs="?",
        help="Optional context name; omit to choose from a numbered list"
    )
    context_show_parser.add_argument(
        "--vision",
        action="store_true",
        help="Show only the optional vision document"
    )
    context_show_parser.set_defaults(func=context_show)

    context_bind_parser = context_commands.add_parser(
        "bind",
        help="Recognize and bind a local context path"
    )
    context_bind_parser.add_argument(
        "first",
        nargs="?",
        metavar="PATH|NAME",
        help="Path to infer, or context name when followed by PATH"
    )
    context_bind_parser.add_argument(
        "second",
        nargs="?",
        metavar="PATH",
        help="Path for an explicitly named context"
    )
    context_bind_parser.add_argument(
        "--as",
        dest="binding_type",
        choices=("source", "production"),
        help="Match only a source or production path"
    )
    context_bind_parser.set_defaults(func=context_bind)

    context_add_parser = context_commands.add_parser(
        "add",
        help="Interactively create a project, person, or machine context",
        description="Interactively create a project, person, or machine context."
    )
    context_add_parser.add_argument(
        "context_type",
        nargs="?",
        choices=("machine",),
        help="Optionally create a machine context directly"
    )
    context_add_parser.add_argument(
        "name",
        nargs="?",
        help="Optional machine context name"
    )
    context_add_parser.add_argument(
        "-a",
        "--agent",
        choices=AGENT_CHOICES,
        help="Choose the AI agent used to draft a project context"
    )
    context_add_parser.set_defaults(func=context_add)

    context_mod_parser = context_commands.add_parser(
        "mod",
        help="Interactively add information to a person context",
        description=(
            "Add information under a Markdown category in a person context. "
            "Projects are not supported yet."
        )
    )
    context_mod_parser.add_argument(
        "name",
        nargs="?",
        help="Optional person context name; omit to choose from a numbered list"
    )
    context_mod_parser.set_defaults(func=context_mod)

    context_delete_parser = context_commands.add_parser(
        "delete",
        help="Archive a context so RotBot no longer accesses it",
        description=(
            "Archive a project, person, or machine context. Archived contexts "
            "are retained but are not loaded or matched by RotBot."
        )
    )
    context_delete_parser.add_argument(
        "name",
        nargs="?",
        help="Optional context name; omit to choose from a numbered list"
    )
    context_delete_parser.set_defaults(func=context_delete)

    sr_parser = commands.add_parser("sr", help="Signal Rot commands")
    sr_commands = sr_parser.add_subparsers(
        dest="sr_command",
        required=True
    )

    status_parser = sr_commands.add_parser(
        "status",
        help="Check Signal Rot website status"
    )
    status_parser.set_defaults(func=sr_status)

    context_parser = sr_commands.add_parser(
        "context",
        help="Show or refresh signalrot context"
    )
    context_parser.add_argument(
        "--refresh",
        action="store_true",
        help="Inspect signalrot and regenerate current-state context"
    )
    _add_agent_argument(context_parser)
    _add_note_argument(context_parser)
    context_parser.set_defaults(func=sr_context)

    diff_parser = sr_commands.add_parser(
        "diff",
        help="Compare the signalrot repository with the live website"
    )
    _add_agent_argument(diff_parser)
    _add_note_argument(diff_parser)
    diff_parser.set_defaults(func=sr_diff)

    pull_parser = sr_commands.add_parser(
        "pull",
        help="Pull the latest Signal Rot version"
    )
    pull_parser.add_argument(
        "--review",
        action="store_true",
        help="Review incoming changes with the AI agent before pulling"
    )
    _add_agent_argument(pull_parser)
    _add_note_argument(pull_parser)
    pull_parser.set_defaults(func=sr_pull)

    push_parser = sr_commands.add_parser(
        "push",
        help="Push the latest Signal Rot version"
    )
    push_parser.add_argument(
        "--review",
        action="store_true",
        help="Review changes with the AI agent before pushing"
    )
    _add_agent_argument(push_parser)
    _add_note_argument(push_parser)
    push_parser.set_defaults(func=sr_push)

    publish_parser = sr_commands.add_parser(
        "publish",
        help="Publish the latest Signal Rot version"
    )
    publish_parser.add_argument(
        "--review",
        action="store_true",
        help="Review the deployment plan with the AI agent before publishing"
    )
    _add_agent_argument(publish_parser)
    _add_note_argument(publish_parser)
    publish_parser.set_defaults(func=sr_publish)

    return parser


def parse_args(argv=None):
    return create_parser().parse_args(argv)
