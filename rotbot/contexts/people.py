import json
import os
from pathlib import Path
import tomllib
from typing import NamedTuple

from rotbot.contexts import loader


PERSON_ROLES = ("contact", "user", "assistant")
CORE_TEMPLATES = {
    "identity.md": (
        "# Identity\n\n"
        "<!-- Relatively stable information describing who this person is. -->\n\n"
        "## Background\n\n"
        "<!-- Occupation, education, location, personal history, and other relevant life context. -->\n\n"
        "## Skills and Knowledge\n\n"
        "<!-- Subjects this person understands and abilities they have developed. -->\n\n"
        "## Interests\n\n"
        "<!-- Hobbies, subjects, communities, and activities this person regularly cares about. -->\n\n"
        "## Traits\n\n"
        "<!-- Stable characteristics, tendencies, and qualities that are useful for understanding this person. -->\n\n"
        "## Important Details\n\n"
        "<!-- Significant facts about this person that should remain easy to find. -->\n\n"
        "## Other\n\n"
        "<!-- Relevant identity information that does not fit naturally into another section. -->\n"
    ),
    "preferences.md": (
        "# Preferences\n\n"
        "<!-- This person's preferences, habits, and preferred ways of communicating or working. -->\n\n"
        "## Communication\n\n"
        "<!-- Preferred tone, level of detail, communication methods, and conversational habits. -->\n\n"
        "## Collaboration\n\n"
        "<!-- How this person prefers to work with others, make decisions, review work, and receive feedback. -->\n\n"
        "## Tools and Workflows\n\n"
        "<!-- Preferred software, platforms, processes, and ways of organizing or completing work. -->\n\n"
        "## Likes and Dislikes\n\n"
        "<!-- Relevant tastes, aversions, favorites, and strong preferences. -->\n\n"
        "## Accessibility and Accommodations\n\n"
        "<!-- Accessibility needs, accommodations, or circumstances that should influence interactions and planning. -->\n\n"
        "## Other\n\n"
        "<!-- Relevant preferences that do not fit naturally into another section. -->\n"
    ),
    "relationship.md": (
        "# Relationship\n\n"
        "<!-- How this person relates to the active user and the context they share. -->\n\n"
        "## Connection\n\n"
        "<!-- How this person and the active user know one another and the nature of their relationship. -->\n\n"
        "## Shared History\n\n"
        "<!-- Important experiences, projects, events, or background shared by both people. -->\n\n"
        "## Personal Dynamic\n\n"
        "<!-- How the two people interact personally, including recurring patterns or useful interpersonal context. -->\n\n"
        "## Working Dynamic\n\n"
        "<!-- How the two people communicate, collaborate, divide work, review decisions, or solve problems together. -->\n\n"
        "## Shared Responsibilities\n\n"
        "<!-- Ongoing projects, obligations, commitments, or areas of responsibility involving both people. -->\n\n"
        "## Boundaries\n\n"
        "<!-- Relevant personal, professional, communication, or privacy boundaries within the relationship. -->\n\n"
        "## Other\n\n"
        "<!-- Relevant relationship information that does not fit naturally into another section. -->\n"
    ),
    "state.md": (
        "# State\n\n"
        "<!-- Current, temporary, or ongoing information involving this person. -->\n\n"
        "## Current Circumstances\n\n"
        "<!-- What is currently happening in this person's life, work, environment, or situation. -->\n\n"
        "## Active Work\n\n"
        "<!-- Projects, tasks, responsibilities, or problems currently receiving this person's attention. -->\n\n"
        "## Upcoming\n\n"
        "<!-- Planned events, deadlines, appointments, trips, or expected changes. -->\n\n"
        "## Open Items\n\n"
        "<!-- Pending decisions, unresolved questions, awaited responses, or incomplete matters. -->\n\n"
        "## Recent Changes\n\n"
        "<!-- Recent events or developments that affect the person's current context. -->\n\n"
        "## Other\n\n"
        "<!-- Current or temporary information that does not fit naturally into another section. -->\n"
    )
}
USER_TEMPLATES = {
    "experience.md": (
        "# Experience\n\n"
        "<!-- The user's accumulated knowledge, abilities, training, and practical experience. -->\n\n"
        "## Professional\n\n"
        "<!-- Roles, industries, responsibilities, accomplishments, and other professional experience. -->\n\n"
        "## Technical\n\n"
        "<!-- Experience with technologies, tools, systems, programming, engineering, or technical problem-solving. -->\n\n"
        "## Creative\n\n"
        "<!-- Experience with music, writing, photography, design, art, performance, or other creative practices. -->\n\n"
        "## Practical\n\n"
        "<!-- Hands-on skills, crafts, hobbies, outdoor abilities, maintenance skills, and other real-world capabilities. -->\n\n"
        "## Education and Training\n\n"
        "<!-- Formal education, certifications, courses, mentorship, and structured training. -->\n\n"
        "## Learning\n\n"
        "<!-- Subjects and skills the user is currently developing, including their present level of familiarity. -->\n\n"
        "## Other\n\n"
        "<!-- Relevant experience that does not fit naturally into another section. -->\n"
    ),
    "priorities.md": (
        "# Priorities\n\n"
        "<!-- The goals, responsibilities, constraints, and areas of focus that currently shape the user's decisions. -->\n\n"
        "## Current Goals\n\n"
        "<!-- Specific outcomes the user is actively trying to achieve. -->\n\n"
        "## Ongoing Responsibilities\n\n"
        "<!-- Recurring work, obligations, relationships, and areas that regularly require attention. -->\n\n"
        "## Areas of Focus\n\n"
        "<!-- Subjects, projects, or activities currently receiving significant time and energy. -->\n\n"
        "## Constraints\n\n"
        "<!-- Time, money, health, technical, logistical, or situational limitations that may affect recommendations and plans. -->\n\n"
        "## Later\n\n"
        "<!-- Ideas, projects, and goals worth retaining but not currently active. -->\n\n"
        "## Other\n\n"
        "<!-- Relevant priorities that do not fit naturally into another section. -->\n"
    )
}


class PersonContextError(Exception):
    pass


class PersonContext(NamedTuple):
    name: str
    role: str
    display_name: str
    related_projects: tuple = ()


class PersonDocument(NamedTuple):
    filename: str
    sections: tuple


def _people_root(people_root=None):
    root = Path(people_root) if people_root is not None else loader.CONTEXT_ROOT / "people"
    if root.is_symlink() or not root.is_dir():
        raise PersonContextError(f"Invalid people context directory: {root}")
    for role in PERSON_ROLES:
        role_root = root / role
        if role_root.is_symlink() or not role_root.is_dir():
            raise PersonContextError(f"Invalid {role} person directory: {role_root}")
    return root


def _find_person_directory(name, root):
    try:
        loader.validate_context_name(name)
    except loader.ContextError as error:
        raise PersonContextError(str(error)) from None
    matches = []
    for role in PERSON_ROLES:
        directory = root / role / name
        if os.path.lexists(directory):
            matches.append((role, directory))
    if not matches:
        raise PersonContextError(f"Unknown or invalid person context: {name}")
    if len(matches) > 1:
        raise PersonContextError(
            f"Person context name exists in multiple role directories: {name}"
        )
    role, directory = matches[0]
    if directory.is_symlink() or not directory.is_dir():
        raise PersonContextError(f"Unknown or invalid person context: {name}")
    return role, directory


def person_context_directory(person, *, people_root=None):
    root = _people_root(people_root)
    role, directory = _find_person_directory(person.name, root)
    if role != person.role:
        raise PersonContextError(f"Person role directory does not match metadata: {person.name}")
    return directory


def _normalize_related_projects(related_projects):
    if related_projects is None:
        return ()
    if isinstance(related_projects, (str, bytes)):
        raise PersonContextError("Related projects must be a collection of names.")
    try:
        projects = tuple(related_projects)
    except TypeError:
        raise PersonContextError("Related projects must be a collection of names.") from None
    normalized = []
    seen = set()
    for project in projects:
        if not isinstance(project, str):
            raise PersonContextError("Related project names must be strings.")
        try:
            loader.validate_context_name(project)
        except loader.ContextError:
            raise PersonContextError(f"Invalid related project name: {project}") from None
        if project not in seen:
            normalized.append(project)
            seen.add(project)
    return tuple(normalized)


def build_person_context(
    name,
    role,
    display_name=None,
    related_projects=None
):
    try:
        loader.validate_context_name(name)
    except loader.ContextError as error:
        raise PersonContextError(str(error)) from None
    if role not in PERSON_ROLES:
        raise PersonContextError(f"Unsupported person role: {role}")
    if display_name is None:
        display_name = name
    if (
        not isinstance(display_name, str)
        or not display_name
        or any(ord(character) < 32 for character in display_name)
    ):
        raise PersonContextError("Invalid person display name.")
    return PersonContext(
        name=name,
        role=role,
        display_name=display_name,
        related_projects=_normalize_related_projects(related_projects)
    )


def render_person_files(person):
    metadata = (
        'type = "person"\n'
        f"role = {json.dumps(person.role, ensure_ascii=False)}\n"
        f"name = {json.dumps(person.name, ensure_ascii=False)}\n"
        f"display_name = {json.dumps(person.display_name, ensure_ascii=False)}\n"
        "related_projects = "
        f"{json.dumps(list(person.related_projects), ensure_ascii=False)}\n"
    )
    files = {"metadata.toml": metadata, **CORE_TEMPLATES}
    if person.role == "user":
        files.update(USER_TEMPLATES)
    return files


def person_document_names(person):
    return tuple(
        filename
        for filename in render_person_files(person)
        if filename.endswith(".md")
    )


def _strip_markdown_comment(line, in_comment):
    output = ""
    remaining = line
    while remaining:
        if in_comment:
            end = remaining.find("-->")
            if end < 0:
                return output, True
            remaining = remaining[end + 3:]
            in_comment = False
            continue
        start = remaining.find("<!--")
        if start < 0:
            return output + remaining, False
        output += remaining[:start]
        remaining = remaining[start + 4:]
        in_comment = True
    return output, in_comment


def _populated_sections(markdown, filename):
    sections = []
    heading = None
    body = []
    in_comment = False
    fence = None

    def finish_section():
        content = "".join(body).strip()
        if content:
            sections.append((heading, content))

    for raw_line in markdown.splitlines(keepends=True):
        stripped = raw_line.strip()
        if fence is not None:
            body.append(raw_line)
            if stripped.startswith(fence):
                fence = None
            continue

        line, in_comment = _strip_markdown_comment(raw_line, in_comment)
        stripped = line.strip()
        if in_comment and not stripped:
            continue
        if stripped.startswith("```") or stripped.startswith("~~~"):
            fence = stripped[:3]
            body.append(line)
            continue
        if line.startswith("## "):
            finish_section()
            heading = line[3:].strip()
            body = []
            continue
        if heading is None and line.startswith("# ") and not "".join(body).strip():
            continue
        body.append(line)
    if in_comment:
        raise PersonContextError(f"Unterminated Markdown comment: {filename}")
    if fence is not None:
        raise PersonContextError(f"Unterminated Markdown fence: {filename}")
    finish_section()
    return tuple(sections)


def load_person_documents(name, *, people_root=None):
    root = _people_root(people_root)
    person = load_person_context(name, people_root=root)
    directory = person_context_directory(person, people_root=root)
    documents = []
    for filename in person_document_names(person):
        path = directory / filename
        if path.is_symlink() or not path.is_file():
            raise PersonContextError(f"Invalid person document: {name}/{filename}")
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            raise PersonContextError(
                f"Could not load person document '{name}/{filename}': {error}"
            ) from None
        documents.append(
            PersonDocument(filename, _populated_sections(content, filename))
        )
    return person, tuple(documents)


def load_person_context(name, *, people_root=None):
    root = _people_root(people_root)
    directory_role, directory = _find_person_directory(name, root)
    metadata_path = directory / "metadata.toml"
    if (
        directory.is_symlink()
        or not directory.is_dir()
        or metadata_path.is_symlink()
        or not metadata_path.is_file()
    ):
        raise PersonContextError(f"Unknown or invalid person context: {name}")
    try:
        metadata = tomllib.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as error:
        raise PersonContextError(
            f"Could not load person context '{name}': {error}"
        ) from None
    if (
        metadata.get("type") != "person"
        or metadata.get("name") != name
        or "display_name" not in metadata
    ):
        raise PersonContextError(f"Invalid person metadata: {name}")
    related_projects = metadata.get("related_projects", [])
    if not isinstance(related_projects, list):
        raise PersonContextError(f"Invalid related projects metadata: {name}")
    person = build_person_context(
        metadata.get("name"),
        metadata.get("role"),
        metadata.get("display_name"),
        related_projects
    )
    if person.role != directory_role:
        raise PersonContextError(f"Person role directory does not match metadata: {name}")
    for filename in render_person_files(person):
        document = directory / filename
        if document.is_symlink() or not document.is_file():
            raise PersonContextError(f"Invalid person document: {name}/{filename}")
    return person


def list_person_contexts(*, people_root=None):
    root = _people_root(people_root)
    entries = []
    for role in PERSON_ROLES:
        try:
            entries.extend((role, entry) for entry in (root / role).iterdir())
        except OSError as error:
            raise PersonContextError(f"Could not list person contexts: {error}") from None
    names = [entry.name for _role, entry in entries if entry.is_dir() and not entry.is_symlink()]
    duplicate = next((name for name in names if names.count(name) > 1), None)
    if duplicate is not None:
        raise PersonContextError(
            f"Person context name exists in multiple role directories: {duplicate}"
        )
    person_contexts = []
    for _role, entry in entries:
        try:
            person_contexts.append(load_person_context(entry.name, people_root=root))
        except PersonContextError:
            continue
    return tuple(sorted(person_contexts, key=lambda person: person.name))


def _write_document(path, content):
    with path.open("x", encoding="utf-8") as destination:
        destination.write(content)
        destination.flush()
        os.fsync(destination.fileno())


def _rollback_person(destination, filenames):
    errors = []
    for filename in filenames:
        try:
            (destination / filename).unlink(missing_ok=True)
        except OSError as error:
            errors.append(str(error))
    try:
        destination.rmdir()
    except OSError as error:
        errors.append(str(error))
    return tuple(errors)


def create_person_context(
    name,
    role,
    display_name=None,
    related_projects=None,
    *,
    people_root=None
):
    person = build_person_context(name, role, display_name, related_projects)
    files = render_person_files(person)
    root = _people_root(people_root)

    destinations = tuple(root / role / person.name for role in PERSON_ROLES)
    if any(os.path.lexists(destination) for destination in destinations):
        raise PersonContextError(f"Person context '{person.name}' already exists.")
    destination = root / person.role / person.name

    created = False
    try:
        destination.mkdir()
        created = True
        for filename, content in files.items():
            _write_document(destination / filename, content)
    except BaseException as error:
        rollback_errors = _rollback_person(destination, files) if created else ()
        if isinstance(error, (KeyboardInterrupt, SystemExit)):
            raise
        message = f"Could not create person context '{person.name}': {error}"
        if rollback_errors:
            message += "\nRollback could not remove all new artifacts:\n"
            message += "\n".join(rollback_errors)
        raise PersonContextError(message) from None
    return destination
