import argparse

from git_commands import git_pull, git_push
from opencode_runner import ask_opencode
from signalrot import sr_diff, sr_publish, sr_pull, sr_push, sr_status
from wtf_report import directory_report


def _add_note_argument(command_parser):
    command_parser.add_argument(
        "-n",
        "--note",
        help="Add a user caveat or request to the OpenCode prompt"
    )


def create_parser():
    parser = argparse.ArgumentParser(
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
        help="Ask OpenCode a question"
    )
    ask_parser.add_argument(
        "question",
        nargs="+",
        help="Question to send to OpenCode"
    )
    ask_parser.set_defaults(func=ask_opencode)

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
        help="Ask OpenCode to review changes before committing"
    )
    push_parser.add_argument(
        "-m",
        "--message",
        help="Use this commit message instead of prompting"
    )
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
    _add_note_argument(wtf_parser)
    wtf_parser.set_defaults(func=directory_report)

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

    diff_parser = sr_commands.add_parser(
        "diff",
        help="Compare the Signal Rot repository with the live website"
    )
    _add_note_argument(diff_parser)
    diff_parser.set_defaults(func=sr_diff)

    pull_parser = sr_commands.add_parser(
        "pull",
        help="Pull the latest Signal Rot version"
    )
    pull_parser.add_argument(
        "--review",
        action="store_true",
        help="Review incoming changes with OpenCode before pulling"
    )
    _add_note_argument(pull_parser)
    pull_parser.set_defaults(func=sr_pull)

    push_parser = sr_commands.add_parser(
        "push",
        help="Push the latest Signal Rot version"
    )
    push_parser.add_argument(
        "--review",
        action="store_true",
        help="Review changes with OpenCode before pushing"
    )
    _add_note_argument(push_parser)
    push_parser.set_defaults(func=sr_push)

    publish_parser = sr_commands.add_parser(
        "publish",
        help="Publish the latest Signal Rot version"
    )
    publish_parser.add_argument(
        "--review",
        action="store_true",
        help="Review the deployment plan with OpenCode before publishing"
    )
    _add_note_argument(publish_parser)
    publish_parser.set_defaults(func=sr_publish)

    return parser


def parse_args(argv=None):
    return create_parser().parse_args(argv)
