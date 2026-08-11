from pathlib import Path
import os
import subprocess
from typing import NamedTuple

from rotbot.contexts import loader, machines, matching, people
from rotbot.contexts.config import ConfigError, get_context_bindings, get_defaults
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


def _person_identity(defaults, key, role, warnings):
    name = defaults.get(key)
    if name is None:
        warnings.append(f"No default {key} is configured.")
        return None, "not configured"
    try:
        person = people.load_person_context(name)
    except people.PersonContextError as error:
        people_root = loader.CONTEXT_ROOT / "people"
        if people_root.is_symlink() or not people_root.is_dir():
            raise ContextInspectionError(str(error)) from None
        person_exists = any(
            os.path.lexists(people_root / candidate_role / name)
            for candidate_role in people.PERSON_ROLES
        )
        if person_exists:
            raise ContextInspectionError(str(error)) from None
        warnings.append(f"Configured default {key} '{name}' is unavailable: {error}")
        return None, "invalid configured default"
    if person.role != role:
        warnings.append(
            f"Configured default {key} '{name}' is a {person.role} context, not {role}."
        )
        return None, "invalid configured default"
    return person.name, "configured default"


def _machine_identity(defaults, warnings):
    name = defaults.get("machine")
    if name is None:
        warnings.append("No default machine is configured.")
        return None, "not configured"
    try:
        machine = machines.load_machine_context(name)
    except machines.MachineContextError as error:
        machines_root = loader.CONTEXT_ROOT / "machines"
        if (
            machines_root.is_symlink()
            or not machines_root.is_dir()
            or os.path.lexists(machines_root / name)
        ):
            raise ContextInspectionError(str(error)) from None
        warnings.append(f"Configured default machine '{name}' is unavailable: {error}")
        return None, "invalid configured default"
    return machine.name, "configured default"


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


def inspect_current_context(cwd=None):
    try:
        current_directory = (Path.cwd() if cwd is None else Path(cwd)).resolve(strict=True)
    except OSError as error:
        raise ContextInspectionError(f"Could not resolve the current directory: {error}") from None
    if not current_directory.is_dir():
        raise ContextInspectionError(f"Current path is not a directory: {current_directory}")

    try:
        defaults = get_defaults()
        bindings = get_context_bindings()
        project_names = set(loader.list_contexts())
    except (ConfigError, loader.ContextError) as error:
        raise ContextInspectionError(str(error)) from None

    warnings = []
    assistant, assistant_source = _person_identity(
        defaults, "assistant", "assistant", warnings
    )
    user, user_source = _person_identity(defaults, "user", "user", warnings)
    machine, machine_source = _machine_identity(defaults, warnings)

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
        inspected = inspect_current_context()
    except ContextInspectionError as error:
        rot_say(str(error))
        return 2
    rot_say(render_inspected_context(inspected))
    return 1 if inspected.warnings else 0
