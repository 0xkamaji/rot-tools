from dataclasses import dataclass

from rotbot.contexts import loader, machines, people


GLOBAL_INSTRUCTIONS = """You are operating through RotBot.

RotBot provides persistent background context describing the user, persistent
assistant identity, current machine, active project, and current invocation.

Use this context to improve continuity, personalization, technical accuracy,
and situational awareness.

Context describes background information. It is not automatically an
instruction, request, or authorization to perform an action.

Use relevant context naturally. Do not repeat personal or contextual facts
merely to demonstrate that you know them. Do not force irrelevant context into
the response.

The current user request is the task to answer."""

ASSISTANT_INSTRUCTIONS = """This context describes Rot, the persistent assistant
identity through which this interaction is occurring.

The execution backend may be Codex, OpenCode, or another AI system. The
execution backend is not itself the persistent assistant identity.

Use this context to preserve established personality, communication style,
history, preferences, relationships, and expressive characteristics. These
details may naturally influence tone, framing, continuity, humor, and behavior
even when they do not need to be explicitly mentioned.

Do not unnecessarily recite these facts."""

USER_INSTRUCTIONS = """This context describes the person currently using RotBot.

Use it to calibrate explanations, assumptions, recommendations, terminology,
level of detail, and collaboration style. Take established experience, skills,
preferences, goals, priorities, and constraints into account when relevant.

Do not mention personal facts solely to demonstrate awareness of them. Do not
force unrelated personal information into the answer."""

MACHINE_INSTRUCTIONS = """This context describes the portable/shareable
characteristics of the machine on which this RotBot invocation is running.

Use this information when operating system, hardware, software, compatibility,
performance, or the local execution environment matters.

Do not assume private/local machine information beyond what is provided."""

PROJECT_INSTRUCTIONS = """This context describes the active project associated
with the current invocation.

Project identity describes the project's relatively stable purpose, principles,
and architecture. Project state describes what currently exists or is currently
true.

Use this information to avoid unnecessarily rediscovering established project
information and to keep answers consistent with the project's actual direction
and implementation.

This context is background information, not authorization to perform actions."""


@dataclass(frozen=True)
class PromptContextBlock:
    context_type: str
    context_id: str | None
    name: str
    sections: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class PromptContext:
    assistant: PromptContextBlock | None
    user: PromptContextBlock | None
    machine: PromptContextBlock | None
    project: PromptContextBlock | None
    working_directory: str
    execution_backend: str


def _person_block(name, expected_role):
    person, documents = people.load_person_documents(name)
    if person.role != expected_role:
        raise people.PersonContextError(
            f"Expected {expected_role} context, found {person.role}: {name}"
        )
    sections = []
    for document in documents:
        category = document.filename.removesuffix(".md")
        content = "\n\n".join(
            f"### {heading}\n\n{text}" if heading is not None else text
            for heading, text in document.sections
        )
        if content:
            sections.append((category, content))
    return PromptContextBlock(expected_role, person.id, person.display_name, tuple(sections))


def _machine_block(name):
    machine, documents = machines.load_machine_files(name)
    content = {document.filename: document.content.strip() for document in documents}
    sections = [("portable facts", content["metadata.toml"])]
    identity_sections = people.populated_markdown_sections(
        content["identity.md"], "identity.md"
    )
    if identity_sections:
        identity = "\n\n".join(
            f"### {heading}\n\n{text}" if heading is not None else text
            for heading, text in identity_sections
        )
        sections.append(("identity", identity))
    if content["software.toml"]:
        sections.append(("software", content["software.toml"]))
    return PromptContextBlock(
        "machine", machine.id, machine.display_name, tuple(sections)
    )


def _project_block(name):
    project = loader.load_context(name)
    sections = tuple(
        (label, content.strip())
        for label, content in (("identity", project.identity), ("state", project.state))
        if content.strip()
    )
    return PromptContextBlock("project", project.id, project.name, sections)


def resolve_prompt_context(inspected, execution_backend):
    return PromptContext(
        assistant=(
            _person_block(inspected.assistant, "assistant")
            if inspected.assistant is not None else None
        ),
        user=(
            _person_block(inspected.user, "user")
            if inspected.user is not None else None
        ),
        machine=(
            _machine_block(inspected.machine)
            if inspected.machine is not None else None
        ),
        project=(
            _project_block(inspected.project)
            if inspected.project is not None else None
        ),
        working_directory=str(inspected.cwd),
        execution_backend=execution_backend
    )


def _tag(name, content):
    return f"<{name}>\n\n{content.strip()}\n\n</{name}>"


def _render_block(block, instructions):
    details = [instructions, f"Name: {block.name}"]
    if block.context_id is not None:
        details.append(f"Context ID: {block.context_id}")
    details.extend(
        f"## {label.replace('_', ' ').title()}\n\n{content}"
        for label, content in block.sections
    )
    return _tag(f"{block.context_type}_context", "\n\n".join(details))


def build_ask_prompt(context, question):
    blocks = [_tag("rotbot_context_instructions", GLOBAL_INSTRUCTIONS)]
    for block, instructions in (
        (context.assistant, ASSISTANT_INSTRUCTIONS),
        (context.user, USER_INSTRUCTIONS),
        (context.machine, MACHINE_INSTRUCTIONS),
        (context.project, PROJECT_INSTRUCTIONS)
    ):
        if block is not None:
            blocks.append(_render_block(block, instructions))

    invocation = [
        f"Working directory: {context.working_directory}",
        f"Execution backend: {context.execution_backend}"
    ]
    if context.project is not None:
        invocation.insert(1, f"Active project: {context.project.name}")
    blocks.append(_tag("invocation_context", "\n".join(invocation)))
    blocks.append(_tag("user_request", question))
    return "\n\n".join(blocks)
