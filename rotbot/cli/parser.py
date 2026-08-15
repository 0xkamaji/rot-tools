import argparse

from rotbot.agents.config import AGENT_CHOICES
from rotbot.agents.runner import ask_agent
from rotbot.commands.ai import ai_context_preview, ai_session_show, ai_sessions
from rotbot.commands.debug import (
    debug_ask,
    debug_last_ask,
    debug_session_register
)
from rotbot.commands.git import git_pull, git_push, git_start, git_status
from rotbot.commands.machine import machine_inspect
from rotbot.commands.privacy import privacy_inspect
from rotbot.contexts.binding import context_bind
from rotbot.contexts.creation import context_add
from rotbot.contexts.deletion import context_delete
from rotbot.contexts.loader import context_list, context_show
from rotbot.contexts.learning import learn_command
from rotbot.contexts.menu import context_menu
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


def _add_agent_argument(command_parser):
    command_parser.add_argument(
        "-a",
        "--agent",
        choices=AGENT_CHOICES,
        help="Choose the AI agent for this command"
    )


def _add_git_push_arguments(command_parser):
    command_parser.add_argument(
        "-m",
        "--message",
        help="Use this commit message instead of prompting"
    )


def show_command_help(args):
    args.command_parser.print_help()
    return 0


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
            "  rot git pull\n"
            "  rot git push -m \"Update project\"\n"
            "  rot git status\n"
            "  rotbot ask \"What is today's date?\"\n"
            "  rot ask \"What is today's date?\""
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    commands = parser.add_subparsers(dest="command")

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

    debug_parser = commands.add_parser(
        "debug",
        help="Inspect an AI invocation plan without invoking a provider"
    )
    debug_commands = debug_parser.add_subparsers(dest="debug_command")
    debug_parser.set_defaults(func=show_command_help, command_parser=debug_parser)

    for action in ("show", "edit", "save"):
        register_parser = debug_commands.add_parser(
            action,
            help=f"Available only inside an interactive Rot session"
        )
        register_parser.set_defaults(func=debug_session_register)

    debug_ask_parser = debug_commands.add_parser(
        "ask",
        help="Inspect the exact prepared plan for an ask operation"
    )
    debug_ask_parser.add_argument(
        "question",
        nargs="+",
        help="Question to prepare without sending"
    )
    _add_agent_argument(debug_ask_parser)
    debug_ask_parser.set_defaults(func=debug_ask)

    debug_last_parser = debug_commands.add_parser(
        "last",
        help="Inspect session-local LAST operations"
    )
    debug_last_commands = debug_last_parser.add_subparsers(dest="debug_last_command")
    debug_last_parser.set_defaults(
        func=show_command_help, command_parser=debug_last_parser
    )
    debug_last_ask_parser = debug_last_commands.add_parser(
        "ask",
        help="Available only inside an interactive Rot session"
    )
    debug_last_ask_parser.add_argument("instruction", nargs="*")
    debug_last_ask_parser.set_defaults(func=debug_last_ask)

    ai_parser = commands.add_parser(
        "ai",
        help="Inspect locally stored Rot AI conversations"
    )
    ai_commands = ai_parser.add_subparsers(dest="ai_command")
    ai_parser.set_defaults(func=show_command_help, command_parser=ai_parser)

    ai_sessions_parser = ai_commands.add_parser(
        "sessions",
        help="List locally stored Rot AI conversations"
    )
    ai_sessions_parser.set_defaults(func=ai_sessions)

    ai_session_parser = ai_commands.add_parser(
        "session",
        help="Inspect one locally stored Rot AI conversation"
    )
    ai_session_commands = ai_session_parser.add_subparsers(dest="ai_session_command")
    ai_session_parser.set_defaults(
        func=show_command_help,
        command_parser=ai_session_parser
    )

    ai_session_show_parser = ai_session_commands.add_parser(
        "show",
        help="Show metadata and transcript for one Rot conversation"
    )
    ai_session_show_parser.add_argument(
        "id",
        nargs="?",
        help="Optional Rot conversation ID; omit to choose from a numbered list"
    )
    ai_session_show_parser.set_defaults(func=ai_session_show)

    ai_context_parser = ai_commands.add_parser(
        "context",
        help="Preview context that may be sent to an AI backend"
    )
    ai_context_commands = ai_context_parser.add_subparsers(dest="ai_context_command")
    ai_context_parser.set_defaults(
        func=show_command_help,
        command_parser=ai_context_parser
    )
    ai_context_preview_parser = ai_context_commands.add_parser(
        "preview",
        help="Preview resolved general AI context without invoking a backend"
    )
    ai_context_preview_parser.set_defaults(func=ai_context_preview)

    privacy_parser = commands.add_parser(
        "privacy",
        help="Inspect general and private context file boundaries"
    )
    privacy_commands = privacy_parser.add_subparsers(dest="privacy_command")
    privacy_parser.set_defaults(
        func=show_command_help,
        command_parser=privacy_parser
    )
    privacy_inspect_parser = privacy_commands.add_parser(
        "inspect",
        help="List context filenames by privacy namespace without reading contents"
    )
    privacy_inspect_parser.set_defaults(func=privacy_inspect)

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
        dest="git_command"
    )
    git_parser.set_defaults(func=show_command_help, command_parser=git_parser)

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

    git_start_parser = git_commands.add_parser(
        "start",
        help="Initialize a Git repository and optionally publish it to GitHub"
    )
    git_start_parser.set_defaults(func=git_start)

    machine_parser = commands.add_parser(
        "machine",
        help="Inspect local machine information"
    )
    machine_commands = machine_parser.add_subparsers(
        dest="machine_command"
    )
    machine_parser.set_defaults(
        func=show_command_help,
        command_parser=machine_parser
    )
    machine_inspect_parser = machine_commands.add_parser(
        "inspect",
        help="Inspect this machine and register it on first use"
    )
    machine_inspect_parser.set_defaults(func=machine_inspect)

    context_parser = commands.add_parser(
        "context",
        help="List, show, add, bind, or archive contexts"
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

    context_show_parser = context_commands.add_parser(
        "show",
        help="Show the active session context, or a specific context's knowledge"
    )
    context_show_parser.add_argument(
        "target",
        nargs="?",
        choices=("user", "assistant", "project", "machine", "contact"),
        help="Optional context target: user, assistant, project, machine, or contact"
    )
    context_show_parser.add_argument(
        "name",
        nargs="?",
        help="Contact name (required when target is 'contact')"
    )
    context_show_parser.set_defaults(func=context_show)

    context_bind_parser = context_commands.add_parser(
        "bind",
        help="Bind a saved context to the session or recognize a project path"
    )
    context_bind_parser.add_argument(
        "first",
        nargs="?",
        metavar="TYPE|PATH|NAME",
        help="Context type for a session binding, or a project path/name"
    )
    context_bind_parser.add_argument(
        "second",
        nargs="?",
        metavar="NAME|PATH",
        help="Saved context name, or path for an explicitly named project"
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
        choices=("machine", "user", "assistant"),
        help="Optionally create a machine, user, or assistant context directly"
    )
    context_add_parser.add_argument(
        "name",
        nargs="?",
        help="Optional machine, user, or assistant context name"
    )
    context_add_parser.set_defaults(func=context_add)

    context_learn_parser = context_commands.add_parser(
        "learn",
        help="Teach Rot about the current user, assistant, project, machine, or a contact"
    )
    context_learn_commands = context_learn_parser.add_subparsers(
        dest="context_learn_target",
        metavar="TARGET"
    )
    context_learn_parser.set_defaults(func=show_command_help, command_parser=context_learn_parser)
    for target in ("user", "assistant", "project", "machine"):
        target_parser = context_learn_commands.add_parser(
            target,
            help=f"Teach Rot about the current {target} context"
        )
        target_parser.add_argument(
            "text", nargs="*", help="Exact text to learn; omit to enter it interactively"
        )
        target_parser.set_defaults(
            func=learn_command, learn_action="append", learn_target=target
        )

    learn_contact_parser = context_learn_commands.add_parser(
        "contact",
        help="Teach Rot about a named contact"
    )
    learn_contact_parser.add_argument("name", help="Contact name")
    learn_contact_parser.add_argument(
        "text", nargs="*", help="Exact text to learn; omit to enter it interactively"
    )
    learn_contact_parser.set_defaults(
        func=learn_command, learn_action="append", learn_target="contact"
    )

    context_edit_parser = context_commands.add_parser(
        "edit",
        help="Choose and edit a general or private knowledge category"
    )
    context_edit_commands = context_edit_parser.add_subparsers(
        dest="context_edit_target",
        metavar="TARGET"
    )
    context_edit_parser.set_defaults(func=show_command_help, command_parser=context_edit_parser)
    for target in ("user", "assistant", "project", "machine"):
        edit_target_parser = context_edit_commands.add_parser(
            target,
            help=f"Edit learned knowledge for the current {target} context"
        )
        edit_target_parser.set_defaults(
            func=learn_command, learn_action="edit", learn_target=target
        )

    edit_contact_parser = context_edit_commands.add_parser(
        "contact",
        help="Edit learned knowledge for a named contact"
    )
    edit_contact_parser.add_argument("name", help="Contact name")
    edit_contact_parser.set_defaults(
        func=learn_command, learn_action="edit", learn_target="contact"
    )

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
        dest="sr_command"
    )
    sr_parser.set_defaults(func=show_command_help, command_parser=sr_parser)

    status_parser = sr_commands.add_parser(
        "status",
        help="Check Signal Rot website status"
    )
    status_parser.set_defaults(func=sr_status)

    context_parser = sr_commands.add_parser(
        "context",
        help="Show signalrot context"
    )
    context_parser.add_argument(
        "--full",
        action="store_true",
        help="Show the complete signalrot identity and state context"
    )
    context_parser.set_defaults(func=sr_context)

    diff_parser = sr_commands.add_parser(
        "diff",
        help="Compare the signalrot repository with the live website"
    )
    diff_parser.set_defaults(func=sr_diff)

    pull_parser = sr_commands.add_parser(
        "pull",
        help="Pull the latest Signal Rot version"
    )
    pull_parser.set_defaults(func=sr_pull)

    push_parser = sr_commands.add_parser(
        "push",
        help="Push the latest Signal Rot version"
    )
    _add_git_push_arguments(push_parser)
    push_parser.set_defaults(func=sr_push)

    publish_parser = sr_commands.add_parser(
        "publish",
        help="Publish the latest Signal Rot version"
    )
    _add_git_push_arguments(publish_parser)
    publish_parser.set_defaults(func=sr_publish)

    return parser


def parse_args(argv=None):
    return create_parser().parse_args(argv)
