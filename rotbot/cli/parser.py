import argparse

from rotbot.agents.config import AGENT_CHOICES
from rotbot.agents.runner import ask_agent
from rotbot.commands.git import git_pull, git_push
from rotbot.commands.wtf import directory_report
from rotbot.contexts.binding import context_bind
from rotbot.contexts.creation import context_add
from rotbot.contexts.loader import context_list, context_show
from rotbot.integrations.signalrot.commands import (
    sr_context,
    sr_diff,
    sr_publish,
    sr_pull,
    sr_push,
    sr_status
)
from rotbot.ui.terminal import rot_content_width, rot_say


class RotArgumentParser(argparse.ArgumentParser):
    def _get_formatter(self):
        return self.formatter_class(
            prog=self.prog,
            width=rot_content_width()
        )

    def print_help(self, file=None):
        rot_say(self.format_help().rstrip())

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
    push_parser.add_argument(
        "--review",
        action="store_true",
        help="Ask the AI agent to review changes before committing"
    )
    push_parser.add_argument(
        "-m",
        "--message",
        help="Use this commit message instead of prompting"
    )
    _add_agent_argument(push_parser)
    _add_note_argument(push_parser)
    push_parser.set_defaults(func=git_push)

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

    context_parser = commands.add_parser(
        "context",
        help="List, show, add, or bind contexts"
    )
    context_commands = context_parser.add_subparsers(
        dest="context_command",
        required=True
    )

    context_list_parser = context_commands.add_parser(
        "list",
        help="List available contexts"
    )
    context_list_parser.set_defaults(func=context_list)

    context_show_parser = context_commands.add_parser(
        "show",
        help="Show a context"
    )
    context_show_parser.add_argument("name", help="Context name")
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
        help="Create a context from a local project"
    )
    context_add_parser.add_argument("name", help="New context name")
    context_add_parser.add_argument("path", help="Local project directory")
    _add_agent_argument(context_add_parser)
    context_add_parser.set_defaults(func=context_add)

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
