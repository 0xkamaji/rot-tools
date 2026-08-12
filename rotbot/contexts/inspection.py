from pathlib import Path
import os
import subprocess
from types import SimpleNamespace
from typing import NamedTuple

from rotbot.contexts import loader, machines, matching, people
from rotbot.contexts.config import (
    ConfigError,
    get_context_bindings,
    get_local_context_bindings,
    set_local_context_binding
)
from rotbot.ui.terminal import rot_say


class ContextInspectionError(Exception):
    pass


class IdentificationSources(NamedTuple):
    assistant: str
    user: str
    machine: str
    project: str


class InspectedContext(NamedTuple):
    assistant: str | None
    user: str | None
    machine: str | None
    project: str | None
    cwd: Path
    identification_sources: IdentificationSources
    warnings: tuple[str, ...]


def _available_people(role):
    try:
        return tuple(
            person.name
            for person in people.list_person_contexts()
            if person.role == role
        )
    except people.PersonContextError as error:
        raise ContextInspectionError(str(error)) from None


def _choose_person(role):
    names = _available_people(role)
    label = "users" if role == "user" else "assistants"
    add_number = len(names) + 1
    rot_say(
        (f"Available {label}:\n" if names else f"No existing {label}.\n")
        + "\n".join(
            f"  {index}. {name}" for index, name in enumerate(names, 1)
        )
        + ("\n" if names else "")
        + f"  {add_number}. Add new {role}"
    )
    while True:
        try:
            answer = input("> ").strip()
        except EOFError:
            return None
        if answer.lower() in {"", "exit", "quit", "q"}:
            return None
        if answer.isdigit() and 1 <= int(answer) <= len(names):
            return names[int(answer) - 1]
        if answer.lower() in {"add", "new", f"add {role}"} or answer == str(add_number):
            before = set(names)
            from rotbot.contexts.creation import context_add

            result = context_add(
                SimpleNamespace(context_type=role, name=None, agent=None)
            )
            if result != 0:
                raise ContextInspectionError(
                    f"Could not add a new {role} context (exit code {result})."
                )
            created = tuple(name for name in _available_people(role) if name not in before)
            if len(created) == 1:
                return created[0]
            return None
        rot_say(f"Choose a number from 1 to {add_number}, or exit.")


def _person_identity(bindings, context_type, role, bootstrap, warnings):
    name = bindings.get(context_type)
    stale = False
    if name is not None:
        try:
            person = people.load_person_context(name)
        except people.PersonContextError as error:
            people_root = loader.CONTEXT_ROOT / "people"
            if people_root.is_symlink() or not people_root.is_dir():
                raise ContextInspectionError(str(error)) from None
            if any(
                os.path.lexists(people_root / candidate_role / name)
                for candidate_role in people.PERSON_ROLES
            ):
                raise ContextInspectionError(str(error)) from None
            stale = True
        else:
            if person.role == role:
                return person.name, "local config"
            stale = True

    if not bootstrap:
        warning = (
            f"Configured local {context_type} '{name}' is unavailable."
            if stale
            else f"No local {context_type} is configured."
        )
        warnings.append(warning)
        return None, "stale local config" if stale else "not configured"

    if stale:
        rot_say(f"Configured local {context_type} '{name}' is unavailable.")
    else:
        rot_say(f"No default {context_type} configured.")
    selected = _choose_person(role)
    if selected is None:
        warnings.append(f"No local {context_type} was selected.")
        return None, "not configured"
    try:
        set_local_context_binding(context_type, selected)
    except ConfigError as error:
        raise ContextInspectionError(str(error)) from None
    rot_say(f"Default {context_type} set: {selected}")
    return selected, "local config"


def _machine_identity(bindings, bootstrap, warnings):
    name = bindings.get("machine")
    if name is not None:
        try:
            machine = machines.load_machine_context(name)
        except machines.MachineContextError:
            pass
        else:
            return machine.name, "local config"

    if not bootstrap:
        warning = (
            f"Configured local machine '{name}' is unavailable."
            if name is not None
            else "No local machine is configured."
        )
        warnings.append(warning)
        return None, "stale local config" if name is not None else "not configured"

    if name is not None:
        rot_say(f"Configured local machine '{name}' is unavailable.")
    else:
        rot_say("No local machine configured.")
    rot_say("Inspecting this machine...")
    from rotbot.commands.machine import MachineRegistrationError, register_local_machine

    try:
        machine = register_local_machine()
    except MachineRegistrationError as error:
        raise ContextInspectionError(str(error)) from None
    return machine.name, "local config"


def _contains(binding, cwd):
    return cwd == binding or binding in cwd.parents


def _binding_match(cwd, bindings, binding_type, project_names):
    key = f"{binding_type}_path"
    matches = []
    for name, binding in bindings.items():
        value = binding.get(key)
        if value is None:
            continue
        try:
            path = Path(value).expanduser().resolve()
        except OSError as error:
            raise ContextInspectionError(
                f"Could not resolve configured project path {name}.{key}: {error}"
            ) from None
        if _contains(path, cwd):
            matches.append((name, path))
    if not matches:
        return None, None, ()

    specificity = max(len(path.parts) for _name, path in matches)
    selected = tuple(
        (name, path) for name, path in matches if len(path.parts) == specificity
    )
    if len(selected) > 1:
        names = ", ".join(sorted(name for name, _path in selected))
        return None, f"ambiguous {binding_type} binding", (
            f"Project {binding_type} binding is ambiguous: {names}.",
        )

    name, _path = selected[0]
    if name not in project_names:
        if os.path.lexists(loader.project_context_directory(name)):
            raise ContextInspectionError(f"Unknown or invalid context: {name}")
        return None, f"invalid {binding_type} binding", (
            f"Configured {binding_type} binding refers to missing project context '{name}'.",
        )
    return name, f"{binding_type} binding", ()


def _repository_root(cwd):
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False
        )
    except FileNotFoundError:
        raise ContextInspectionError("Git is not installed or is not available in PATH.") from None
    except OSError as error:
        raise ContextInspectionError(f"Could not inspect the current Git repository: {error}") from None
    if result.returncode != 0:
        return None
    try:
        return Path(result.stdout.strip()).resolve(strict=True)
    except OSError as error:
        raise ContextInspectionError(f"Could not resolve the current Git repository: {error}") from None


def _safe_project_match(cwd):
    repository = _repository_root(cwd)
    if repository is None:
        return None, "no matching project context", ()
    try:
        candidates = matching.match_contexts(
            repository,
            binding_type="source",
            caddy_paths=()
        )
    except (matching.MatchError, loader.ContextError) as error:
        raise ContextInspectionError(str(error)) from None
    strong = tuple(candidate for candidate in candidates if candidate.strong)
    if not strong:
        return None, "no matching project context", ()
    names = tuple(sorted({candidate.name for candidate in strong}))
    if len(names) > 1:
        return None, "ambiguous project match", (
            f"Safe project matching is ambiguous: {', '.join(names)}.",
        )
    return names[0], "project match", ()


def inspect_current_context(cwd=None, bootstrap=False):
    try:
        current_directory = (Path.cwd() if cwd is None else Path(cwd)).resolve(strict=True)
    except OSError as error:
        raise ContextInspectionError(f"Could not resolve the current directory: {error}") from None
    if not current_directory.is_dir():
        raise ContextInspectionError(f"Current path is not a directory: {current_directory}")

    try:
        local_bindings = get_local_context_bindings()
        bindings = get_context_bindings()
        project_names = set(loader.list_contexts())
    except (ConfigError, loader.ContextError) as error:
        raise ContextInspectionError(str(error)) from None

    warnings = []
    user, user_source = _person_identity(
        local_bindings, "user", "user", bootstrap, warnings
    )
    assistant, assistant_source = _person_identity(
        local_bindings, "assistant", "assistant", bootstrap, warnings
    )
    machine, machine_source = _machine_identity(local_bindings, bootstrap, warnings)

    project, project_source, project_warnings = _binding_match(
        current_directory, bindings, "source", project_names
    )
    if project_source is None:
        project, project_source, project_warnings = _binding_match(
            current_directory, bindings, "production", project_names
        )
    if project_source is None:
        project, project_source, project_warnings = _safe_project_match(current_directory)
    warnings.extend(project_warnings)

    return InspectedContext(
        assistant,
        user,
        machine,
        project,
        current_directory,
        IdentificationSources(
            assistant_source,
            user_source,
            machine_source,
            project_source
        ),
        tuple(warnings)
    )


def render_inspected_context(inspected):
    sources = inspected.identification_sources
    lines = [
        "CURRENT ROTBOT CONTEXT",
        "----------------------",
        "",
        f"Assistant:  {inspected.assistant or 'unidentified'}",
        f"User:       {inspected.user or 'unidentified'}",
        f"Machine:    {inspected.machine or 'unidentified'}",
        f"Directory:  {inspected.cwd}",
        f"Project:    {inspected.project or 'none'}",
        "",
        "Identified by:",
        f"  Assistant: {sources.assistant}",
        f"  User:      {sources.user}",
        f"  Machine:   {sources.machine}",
        f"  Project:   {sources.project}",
        "",
        "Local/private machine metadata: excluded"
    ]
    if inspected.warnings:
        lines.extend(("", "Warnings:", *(f"  {warning}" for warning in inspected.warnings)))
    return "\n".join(lines)


def context_inspect(args):
    try:
        inspected = inspect_current_context(bootstrap=True)
    except ContextInspectionError as error:
        rot_say(str(error))
        return 2
    rot_say(render_inspected_context(inspected))
    return 1 if inspected.warnings else 0
