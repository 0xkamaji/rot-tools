from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import tomllib

from rotbot.contexts import entities


CORE_MODES = frozenset({"TALK", "WORK"})


@dataclass(frozen=True)
class AssistantCapabilityPolicy:
    default_mode: str = "TALK"
    talk_enabled: bool = True
    work_enabled: bool = False
    work_scope: str = "active_project"
    revoke_work_on_project_change: bool = True
    valid: bool = False
    error: str | None = None


@dataclass(frozen=True)
class CapabilityState:
    assistant_id: str | None
    mode: str
    project_id: str | None
    work_project_id: str | None
    conversation: bool
    file_read: bool
    file_write: bool
    agent_execution: bool
    policy_valid: bool
    policy_fingerprint: str
    denial_reason: str | None = None


def safe_policy(error=None):
    return AssistantCapabilityPolicy(error=error)


def load_assistant_policy(reference, *, root=None):
    try:
        assistant = entities.load_assistant_context(reference, root=root)
        directory = entities.entity_directory(assistant, root)
        if not directory.exists():
            return safe_policy("Legacy assistant has no canonical capability policy.")
        path = directory / "capabilities.toml"
        if path.is_symlink() or not path.is_file():
            return safe_policy("Assistant capability policy is missing.")
        document = tomllib.loads(path.read_text(encoding="utf-8"))
        interaction = document.get("interaction", {})
        modes = document.get("modes", {})
        transitions = document.get("transitions", {})
        talk = modes.get("talk", {})
        work = modes.get("work", {})
        values = (
            interaction.get("default_mode", "talk"),
            talk.get("enabled", True),
            work.get("enabled", False),
            work.get("scope", "active_project"),
            transitions.get("revoke_work_on_project_change", True)
        )
        if (
            values[0] != "talk"
            or not isinstance(values[1], bool)
            or not isinstance(values[2], bool)
            or values[3] != "active_project"
            or values[4] is not True
        ):
            return safe_policy("Assistant capability policy is invalid.")
        return AssistantCapabilityPolicy(
            default_mode=values[0].upper(),
            talk_enabled=values[1],
            work_enabled=values[2],
            work_scope=values[3],
            revoke_work_on_project_change=values[4],
            valid=True
        )
    except (
        entities.EntityContextError, OSError, UnicodeError,
        tomllib.TOMLDecodeError, AttributeError
    ) as error:
        return safe_policy(str(error))


def resolve_capability_state(
    assistant_id,
    policy,
    requested_mode,
    project_id,
    work_project_id=None
):
    mode = requested_mode if requested_mode in CORE_MODES else "TALK"
    denial = None
    work_allowed = (
        mode == "WORK"
        and policy.valid
        and policy.work_enabled
        and project_id is not None
        and work_project_id == project_id
    )
    if mode == "WORK" and not work_allowed:
        mode = "TALK"
        denial = "WORK is not allowed by the resolved assistant policy and project scope."
    if not policy.valid:
        denial = policy.error or "Assistant capability policy is unavailable."
    fingerprint = hashlib.sha256(json.dumps({
        "assistant_id": assistant_id,
        "mode": mode,
        "project_id": project_id,
        "work_project_id": work_project_id,
        "policy": policy.__dict__
    }, sort_keys=True).encode("utf-8")).hexdigest()
    return CapabilityState(
        assistant_id=assistant_id,
        mode=mode,
        project_id=project_id,
        work_project_id=work_project_id if mode == "WORK" else None,
        conversation=policy.talk_enabled or work_allowed,
        file_read=work_allowed,
        file_write=work_allowed,
        agent_execution=work_allowed,
        policy_valid=policy.valid,
        policy_fingerprint=fingerprint,
        denial_reason=denial
    )
