import argparse
import sys

from git_commands import git_push
from opencode_runner import ask_opencode, directory_report
from signalrot import sr_publish, sr_pull, sr_push, sr_status


COMMANDS = {"push", "sr", "wtf"}


def create_parser():
    parser = argparse.ArgumentParser(
        prog="rotbot",
        description="Launch Rotbot using either the 'rotbot' or 'rot' command.",
        epilog=(
            "Examples:\n"
            "  rotbot sr status\n"
            "  rot sr status\n"
            "  rot push\n"
            "  rot push --review\n"
            "  rot wtf\n"
            "  rot wtf path/to/file.py\n"
            "  rotbot \"What is today's date?\"\n"
            "  rot \"What is today's date?\""
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    commands = parser.add_subparsers(dest="command", required=True)

    push_parser = commands.add_parser(
        "push",
        help="Stage, commit, and push the current Git repository"
    )
    push_parser.add_argument(
        "--review",
        action="store_true",
        help="Ask OpenCode to review changes before committing"
    )
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

    pull_parser = sr_commands.add_parser(
        "pull",
        help="Pull the latest Signal Rot version"
    )
    pull_parser.set_defaults(func=sr_pull)

    push_parser = sr_commands.add_parser(
        "push",
        help="Push the latest Signal Rot version"
    )
    push_parser.set_defaults(func=sr_push)

    publish_parser = sr_commands.add_parser(
        "publish",
        help="Publish the latest Signal Rot version"
    )
    publish_parser.set_defaults(func=sr_publish)

    return parser


def parse_args(argv=None):
    argv = sys.argv[1:] if argv is None else argv

    if argv and argv[0] not in COMMANDS and argv[0] not in {"-h", "--help"}:
        return argparse.Namespace(
            func=ask_opencode,
            question=" ".join(argv)
        )

    return create_parser().parse_args(argv)
