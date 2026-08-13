def _yes_no(value):
    return "yes" if value else "no"


def _context_names(context):
    if context is None:
        return "none"
    names = [
        name
        for name in ("assistant", "user", "machine", "project")
        if getattr(context, name, None) is not None
    ]
    return ", ".join(names) if names else "none"


def render_ai_debug_plan(plan):
    provider_input = plan.provider_input
    evidence = plan.context_material or ""
    lines = [
        "AI DEBUG",
        "No provider was invoked.",
        "",
        "Operation",
        f"  command: {plan.parent_command}",
        f"  purpose: {plan.purpose}",
        "",
        "Execution",
        f"  provider: {plan.provider_name or 'unresolved'}",
        f"  trust: {plan.trust_level}",
        f"  context view: {plan.context_view}",
        f"  model: {plan.model or 'unresolved'}",
        f"  authority: {plan.authority or 'none'}",
        f"  isolated: {_yes_no(plan.isolated)}",
        f"  retries: {plan.retries}",
        f"  timeout: {plan.timeout if plan.timeout is not None else 'none'}",
        f"  preparation error: {plan.preparation_error or 'none'}",
        "",
        "Persistent context",
        f"  available: {_context_names(plan.available_persistent_context)}",
        f"  selected: {_context_names(plan.selected_persistent_context)}",
        f"  sent: {_yes_no(plan.context_sent)}",
        "",
        "Conversation",
        f"  attached: {_yes_no(plan.conversation_id is not None)}",
        f"  available turns: {len(plan.available_conversation)}",
        f"  selected turns: {len(plan.selected_conversation)}",
        f"  sent: {_yes_no(plan.conversation_sent)}",
        f"  provider session state: {'present' if plan.provider_state else 'absent'}",
        "",
        "Task evidence",
        f"  characters: {len(evidence)}",
        f"  bytes: {len(evidence.encode('utf-8'))}",
        evidence or "none",
        "",
        "Output contract",
        plan.output_contract or "none",
        "",
        "Provider input",
        f"  characters: {len(provider_input)}",
        f"  bytes: {len(provider_input.encode('utf-8'))}",
        "",
        "----- EXACT ROT -> PROVIDER INPUT -----",
        "",
        provider_input,
        "",
        "----- END ROT -> PROVIDER INPUT -----"
    ]
    return "\n".join(lines)
