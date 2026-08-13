from datetime import datetime
from pathlib import Path
import uuid

from rotbot.agents.invocation import AIInvocation, invoke, resolve_provider
from rotbot.contexts import entities, loader, machines, people
from rotbot.contexts.inspection import ContextInspectionError, inspect_current_context
from rotbot.contexts.prompt import build_ask_prompt, resolve_egress_context
from rotbot.session.ai import AIMessage
from rotbot.session.conversations import ConversationStore, ConversationStoreError
from rotbot.ui.ai import AIActivityPresenter
from rotbot.ui.terminal import (
    rot_output_end,
    rot_output_line,
    rot_output_start,
    rot_say
)


resolve_prompt_context = resolve_egress_context


def ask_agent(args):
    question = " ".join(args.question) if isinstance(args.question, list) else args.question
    provider, provider_error = resolve_provider(getattr(args, "agent", None))
    if provider is None:
        rot_say(provider_error)
        return 127
    try:
        inspected = inspect_current_context(bootstrap=False)
        context = resolve_prompt_context(inspected, provider.NAME)
        prompt = build_ask_prompt(context, question)
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

    result = invoke(
        AIInvocation(
            purpose="ask",
            parent_command="ask",
            prompt=prompt,
            working_directory=Path(inspected.cwd),
            agent_name=getattr(args, "agent", None),
            conversation=False,
            display_output=True
        ),
        on_event=presenter,
        on_output=output
    )
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
