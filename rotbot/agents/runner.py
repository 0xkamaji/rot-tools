from datetime import datetime
from pathlib import Path
import uuid

from rotbot.agents.invocation import AIRequest, invoke
from rotbot.contexts import entities, loader, machines, people
from rotbot.contexts.inspection import ContextInspectionError, inspect_current_context
from rotbot.session.ai import AIMessage
from rotbot.session.conversations import ConversationStore, ConversationStoreError
from rotbot.ui.ai import AIActivityPresenter
from rotbot.ui.terminal import (
    rot_output_end,
    rot_output_line,
    rot_output_start,
    rot_say
)


def ask_agent(args):
    question = " ".join(args.question) if isinstance(args.question, list) else args.question
    try:
        inspected = inspect_current_context(bootstrap=False)
    except (
        ContextInspectionError,
        loader.ContextError,
        machines.MachineContextError,
        people.PersonContextError,
        entities.EntityContextError
    ) as error:
        rot_say(str(error))
        return 2

    presenter = AIActivityPresenter("thinking")
    output_started = False

    def output(line):
        nonlocal output_started
        if line.strip() and not output_started:
            rot_output_start(question)
            output_started = True
        if output_started:
            rot_output_line(line.rstrip("\r\n"))

    try:
        result = invoke(
            AIRequest(
                purpose="ask",
                parent_command="ask",
                task=question,
                working_directory=Path(inspected.cwd),
                agent_name=getattr(args, "agent", None),
                inspected_context=inspected
            ),
            on_event=presenter,
            on_output=output
        )
    except (
        ContextInspectionError,
        loader.ContextError,
        machines.MachineContextError,
        people.PersonContextError,
        entities.EntityContextError
    ) as error:
        rot_say(str(error))
        return 2
    if output_started:
        rot_output_end()
    if result.validation_error:
        rot_say(result.validation_error)
        return result.returncode or 1
    if not result.output.strip():
        rot_say("The AI agent returned no response.")
    else:
        store = ConversationStore()
        conversation_id = f"rotconv_{uuid.uuid4().hex}"
        now = datetime.now().astimezone()
        try:
            store.create(conversation_id, now, inspected, inspected.cwd, result.provider or "AI")
            store.append_message(
                conversation_id,
                AIMessage(f"msg_{uuid.uuid4().hex}", "user", question, now, "TALK")
            )
            store.append_message(
                conversation_id,
                AIMessage(
                    f"msg_{uuid.uuid4().hex}", "assistant", result.output,
                    datetime.now().astimezone(), "TALK"
                )
            )
            store.close(
                conversation_id,
                datetime.now().astimezone(),
                model="",
                current_cwd=str(inspected.cwd)
            )
        except ConversationStoreError as error:
            rot_say(f"Warning: AI conversation could not be saved.\n{error}")
    rot_say(f"Response received in {result.elapsed:.1f}s.")
    return result.returncode
