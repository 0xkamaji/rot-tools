from types import SimpleNamespace

from rotbot.contexts.binding import context_bind
from rotbot.contexts.creation import context_add
from rotbot.contexts.deletion import context_delete
from rotbot.contexts.loader import context_list, context_show
from rotbot.contexts.modification import context_mod
from rotbot.ui.terminal import rot_say


ACTIONS = (
    ("add", "Create a project or person context"),
    ("list", "List all available project and person contexts"),
    ("mod", "Add categorized information to a person context"),
    ("show", "Choose and display a project or person context"),
    ("bind", "Recognize and bind the current project directory"),
    ("delete", "Choose a context to archive without destroying it")
)
ALIASES = {
    "a": "add",
    "l": "list",
    "m": "mod",
    "modify": "mod",
    "s": "show",
    "b": "bind",
    "d": "delete",
    "archive": "delete"
}


def context_menu(args):
    rot_say(
        "What would you like to do with contexts?\n\n"
        + "\n".join(
            f"  {index}. {name} - {description}"
            for index, (name, description) in enumerate(ACTIONS, 1)
        )
        + f"\n  {len(ACTIONS) + 1}. exit - Leave the context menu"
    )
    while True:
        try:
            answer = input("> ").strip().lower()
        except EOFError:
            return 0
        if answer in {"", "exit", "e", "quit", "q", str(len(ACTIONS) + 1)}:
            rot_say("Context menu closed.")
            return 0
        answer = ALIASES.get(answer, answer)
        if answer.isdigit() and 1 <= int(answer) <= len(ACTIONS):
            selected = ACTIONS[int(answer) - 1]
        else:
            selected = next((action for action in ACTIONS if action[0] == answer), None)
        if selected is not None:
            name, _description = selected
            handler, arguments = {
                "add": (context_add, SimpleNamespace(agent=None)),
                "list": (context_list, SimpleNamespace()),
                "mod": (context_mod, SimpleNamespace(name=None)),
                "show": (context_show, SimpleNamespace(name=None, vision=False)),
                "bind": (
                    context_bind,
                    SimpleNamespace(first=None, second=None, binding_type=None)
                ),
                "delete": (context_delete, SimpleNamespace(name=None))
            }[name]
            return handler(arguments)
        rot_say(
            f"Please choose a number from 1 to {len(ACTIONS) + 1}, "
            "or enter an action name."
        )
