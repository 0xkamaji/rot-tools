from rotbot.session.conversations import ConversationStore, ConversationStoreError
from rotbot.contexts import entities, loader, machines, people, prompt
from rotbot.contexts.inspection import ContextInspectionError, inspect_current_context
from rotbot.ui.terminal import rot_continue, rot_say, rot_table


def _display_time(value):
    return value.astimezone().strftime("%b %d %I:%M %p").replace(" 0", " ")


def ai_context_preview(args):
    try:
        inspected = inspect_current_context(bootstrap=False)
        context = prompt.resolve_egress_context(inspected, "preview")
        builder = getattr(prompt, "build_context_preview", None)
        preview = (
            builder(context)
            if builder is not None
            else "\n\n".join(prompt._context_blocks(context))
        )
    except (
        ContextInspectionError,
        entities.EntityContextError,
        machines.MachineContextError,
        loader.ContextError,
        people.PersonContextError,
        OSError
    ) as error:
        rot_say(f"Could not build AI context preview: {error}")
        return 2

    blocked = (
        ("assistants", inspected.assistant),
        ("users", inspected.user),
        ("machines", inspected.machine),
        ("projects", inspected.project)
    )
    blocked_paths = tuple(
        f"{category}/{name}/private/"
        for category, name in blocked
        if name is not None
    )
    lines = [
        "ROT AI CONTEXT PREVIEW",
        "----------------------",
        preview,
        "",
        "BLOCKED PRIVATE CONTEXT PATHS (NOT SENT)",
        "----------------------------------------",
        *(blocked_paths or ("(none)",))
    ]
    rot_say("\n".join(lines))
    return 0


def ai_sessions(args):
    store = ConversationStore()
    try:
        conversations = store.list()
    except ConversationStoreError as error:
        rot_say(str(error))
        return 2
    active_id = getattr(args, "active_conversation_id", None)
    rot_say("LOCAL ROT AI CONVERSATIONS")
    if not conversations:
        rot_continue("(none)")
        return 0
    rot_table(
        ("ID", "STATUS", "PROJECT", "STARTED"),
        (
            (
                conversation.id + (" *" if conversation.id == active_id else ""),
                "current" if conversation.id == active_id else conversation.status,
                conversation.project_id or "none",
                _display_time(conversation.started_at)
            )
            for conversation in conversations
        ),
        fill=False
    )
    if active_id is not None:
        rot_continue("* current Rot conversation")
    return 0


def ai_session_show(args):
    store = ConversationStore()
    conversation_id = args.id
    try:
        if conversation_id is None:
            conversations = store.list()
            if not conversations:
                rot_say("No local Rot AI conversations found.")
                return 0
            rot_say(
                "CHOOSE A ROT AI CONVERSATION\n"
                "----------------------------\n"
                + "\n".join(
                    f"{index:>3}.  {conversation.project_id or 'none'} · "
                    f"{conversation.status} · {_display_time(conversation.started_at)}"
                    for index, conversation in enumerate(conversations, 1)
                )
            )
            while True:
                try:
                    answer = input("> ").strip().lower()
                except EOFError:
                    return 0
                if answer in {"", "exit", "quit", "q"}:
                    return 0
                if answer.isdigit() and 1 <= int(answer) <= len(conversations):
                    conversation_id = conversations[int(answer) - 1].id
                    break
                rot_say(f"Choose a number from 1 to {len(conversations)}, or exit.")
        conversation = store.load(conversation_id)
    except ConversationStoreError as error:
        rot_say(str(error))
        return 2
    lines = [
        "LOCAL ROT AI CONVERSATION",
        "-------------------------",
        f"ID:          {conversation.id}",
        f"Status:      {conversation.status}",
        f"Started:     {conversation.started_at.isoformat()}",
        f"Closed:      {conversation.closed_at.isoformat() if conversation.closed_at else 'open'}",
        f"User ID:     {conversation.user_id or 'unknown'}",
        f"Assistant:   {conversation.assistant_id or 'unknown'}",
        f"Machine ID:  {conversation.machine_id or 'unknown'}",
        f"Project ID:  {conversation.project_id or 'none'}",
        f"Initial cwd: {conversation.initial_cwd}",
        f"Current cwd: {conversation.current_cwd}",
        f"Backend:     {conversation.backend}",
        f"Model:       {conversation.model or 'unknown'}",
        f"Context:     version {conversation.context_version}",
        "",
        "REMOTE STATE"
    ]
    if conversation.remote_state:
        lines.extend(
            f"{item.get('provider', 'unknown')} {item.get('type', 'state')} "
            f"{item.get('id', 'unknown')} ({item.get('persistence', 'unknown')})"
            for item in conversation.remote_state
        )
    else:
        lines.append("(none)")
    lines.extend(("", "TRANSCRIPT"))
    if conversation.messages:
        for message in conversation.messages:
            lines.extend((
                "",
                f"{message.role} [{message.id}] {message.created_at.isoformat()} "
                f"{message.authority} {message.status}",
                message.content
            ))
    else:
        lines.append("(empty)")
    rot_say("\n".join(lines))
    return 0
