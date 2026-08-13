import ast
from dataclasses import dataclass
import os
from pathlib import Path
import re
import stat


MAX_DEPTH = 3
MAX_STRUCTURE_ENTRIES = 150
MAX_SOURCE_FILES = 24
MAX_SOURCE_BYTES = 64_000
MAX_DOCUMENTATION_BYTES = 1_000
MAX_IDENTITY_EVIDENCE_BYTES = 3_500
MAX_STATE_EVIDENCE_BYTES = 5_000
IGNORED_NAMES = {
    ".git", ".hg", ".svn", ".cache", ".mypy_cache", ".pytest_cache",
    ".tox", ".venv", "venv", "env", "__pycache__", "node_modules",
    "vendor", "dist", "build", "target", "coverage", ".next"
}
SENSITIVE_PARTS = (
    "credential", "secret", "token", "private_key", "id_rsa", "id_ed25519"
)
BINARY_SUFFIXES = {
    ".7z", ".a", ".bin", ".class", ".dll", ".dylib", ".exe", ".gif",
    ".gz", ".ico", ".jar", ".jpeg", ".jpg", ".o", ".pdf", ".png",
    ".pyc", ".so", ".tar", ".webp", ".woff", ".woff2", ".zip"
}
MANIFEST_NAMES = {
    "pyproject.toml", "package.json", "cargo.toml", "go.mod", "composer.json",
    "gemfile", "setup.py", "requirements.txt"
}
ENTRYPOINT_NAMES = {
    "__main__.py", "main.py", "app.py", "cli.py", "index.js", "index.ts",
    "main.go", "main.rs"
}
REPRESENTATIVE_PARTS = {
    "__main__", "main", "cli", "parser", "command", "commands", "context",
    "contexts", "session", "agent", "agents", "invocation", "config", "core"
}
SENSITIVE_CONTENT_PATTERN = re.compile(
    r"(?i)(?:api[_-]?key|access[_-]?token|auth[_-]?token|password|passwd|"
    r"private[_-]?key|client[_-]?secret)\s*[:=]|"
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----|https?://[^\s/:]+:[^\s/@]+@"
)
GENERATED_PATTERN = re.compile(
    r"(?i)^(?:rotbot_context_.*\.txt|rotbot_debug_.*\.txt|.*\.(?:log|tmp|out))$"
)


@dataclass(frozen=True)
class ProjectDevelopmentEvidence:
    project_name: str
    remotes: tuple[str, ...]
    project_type: str | None
    structure: tuple[str, ...]
    entrypoints: tuple[str, ...]
    manifests: tuple[str, ...]
    implementation_facts: tuple[str, ...]
    cli_commands: tuple[str, ...]
    documentation_intro: str | None


def _is_sensitive(name):
    lowered = name.lower()
    return (
        lowered == ".env"
        or lowered.startswith(".env.")
        or Path(lowered).suffix in {".key", ".pem", ".p12", ".pfx"}
        or any(part in lowered for part in SENSITIVE_PARTS)
    )


def _safe_name(path):
    return not (
        path.name in IGNORED_NAMES
        or path.name.startswith(".")
        or _is_sensitive(path.name)
        or path.suffix.lower() in BINARY_SUFFIXES
        or GENERATED_PATTERN.match(path.name)
    )


def _read_text(path, limit):
    descriptor = None
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            return None
        with os.fdopen(descriptor, "rb") as source:
            descriptor = None
            content = source.read(limit + 1)
    except OSError:
        return None
    finally:
        if descriptor is not None:
            os.close(descriptor)
    if len(content) > limit or b"\0" in content[:4096]:
        return None
    try:
        text = content.decode("utf-8")
    except UnicodeError:
        return None
    return None if SENSITIVE_CONTENT_PATTERN.search(text) else text


def _documentation_intro(project):
    readme = next((
        path for path in sorted(project.iterdir(), key=lambda item: item.name.lower())
        if path.name.lower() in {"readme", "readme.md", "readme.rst", "readme.txt"}
        and path.is_file() and not path.is_symlink()
    ), None)
    if readme is None:
        return None
    text = _read_text(readme, MAX_SOURCE_BYTES)
    if text is None:
        return None
    selected = []
    size = 0
    for line in text.splitlines():
        stripped = line.strip()
        if selected and stripped.startswith(("## ", "# Installation", "# Usage")):
            break
        addition = (line + "\n").encode("utf-8")
        if size + len(addition) > MAX_DOCUMENTATION_BYTES:
            break
        selected.append(line)
        size += len(addition)
    intro = "\n".join(selected).strip()
    return intro or None


def _source_priority(relative):
    parts = tuple(part.lower().removesuffix(".py") for part in Path(relative).parts)
    representative = any(part in REPRESENTATIVE_PARTS for part in parts)
    tests = any(part in {"test", "tests"} or part.startswith("test_") for part in parts)
    return (tests, not representative, len(parts), relative.lower(), relative)


def _python_facts(path, relative):
    text = _read_text(path, MAX_SOURCE_BYTES)
    if text is None:
        return (), ()
    try:
        tree = ast.parse(text, filename=relative)
    except (SyntaxError, ValueError):
        return (), ()
    symbols = []
    commands = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            symbols.append(f"class {node.name}")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            symbols.append(f"function {node.name}")
        for child in ast.walk(node):
            if not isinstance(child, ast.Call) or not child.args:
                continue
            function = child.func
            if (
                isinstance(function, ast.Attribute)
                and function.attr == "add_parser"
                and isinstance(child.args[0], ast.Constant)
                and isinstance(child.args[0].value, str)
            ):
                commands.append(child.args[0].value)
    fact = (
        (f"{relative}: " + ", ".join(symbols[:20]),)
        if symbols else ()
    )
    return fact, tuple(commands)


def inspect_project_development_evidence(project, remotes, project_name=None):
    project = Path(project)
    structure = []
    files = []
    queue = [(project, 0)]
    while queue and len(structure) < MAX_STRUCTURE_ENTRIES:
        directory, depth = queue.pop(0)
        try:
            entries = sorted(directory.iterdir(), key=lambda item: (item.name.lower(), item.name))
        except OSError:
            continue
        for path in entries:
            if len(structure) >= MAX_STRUCTURE_ENTRIES:
                break
            try:
                if path.is_symlink() or not _safe_name(path):
                    continue
                relative = path.relative_to(project).as_posix()
                if path.is_dir():
                    structure.append(relative + "/")
                    if depth < MAX_DEPTH - 1:
                        queue.append((path, depth + 1))
                elif path.is_file():
                    structure.append(relative)
                    files.append((relative, path))
            except OSError:
                continue

    manifests = tuple(
        relative for relative, _path in files
        if Path(relative).name.lower() in MANIFEST_NAMES
    )
    entrypoints = tuple(
        relative for relative, _path in files
        if Path(relative).name.lower() in ENTRYPOINT_NAMES
    )
    python_files = sorted(
        ((relative, path) for relative, path in files if path.suffix.lower() == ".py"),
        key=lambda item: _source_priority(item[0])
    )[:MAX_SOURCE_FILES]
    facts = []
    commands = []
    for relative, path in python_files:
        file_facts, file_commands = _python_facts(path, relative)
        facts.extend(file_facts)
        commands.extend(file_commands)

    suffixes = {path.suffix.lower() for _relative, path in files}
    project_type = (
        "Python application" if ".py" in suffixes
        else "JavaScript/TypeScript application" if suffixes & {".js", ".ts", ".tsx"}
        else "Go application" if ".go" in suffixes
        else "Rust application" if ".rs" in suffixes
        else None
    )
    return ProjectDevelopmentEvidence(
        project_name=project_name or project.name,
        remotes=tuple(remotes),
        project_type=project_type,
        structure=tuple(structure),
        entrypoints=entrypoints,
        manifests=manifests,
        implementation_facts=tuple(facts),
        cli_commands=tuple(dict.fromkeys(commands)),
        documentation_intro=_documentation_intro(project)
    )


def _bounded_sections(title, sections, limit):
    output = title
    for heading, values in sections:
        values = tuple(value for value in values if value)
        if not values:
            continue
        block = f"\n\n{heading}\n" + "\n".join(f"- {value}" for value in values)
        if len((output + block).encode("utf-8")) > limit:
            remaining = limit - len((output + f"\n\n{heading}\n").encode("utf-8"))
            selected = []
            for value in values:
                line = f"- {value}\n"
                if len(("".join(selected) + line).encode("utf-8")) > remaining:
                    break
                selected.append(line)
            if selected:
                output += f"\n\n{heading}\n" + "".join(selected).rstrip()
            break
        output += block
    return output


def render_identity_evidence(evidence):
    stable_structure = tuple(
        path for path in evidence.structure
        if not path.startswith("tests/") and "/tests/" not in path
    )
    documentation = (
        (
            "SUPPORTING DOCUMENTATION\n"
            "Documentation may describe purpose or audience and may lag implementation.\n"
            + evidence.documentation_intro,
        ) if evidence.documentation_intro else ()
    )
    return _bounded_sections(
        "PROJECT IDENTITY EVIDENCE\nDerived deterministically from project files.",
        (
            ("Project", (evidence.project_name, evidence.project_type or "type unknown")),
            ("Normalized repository remotes", evidence.remotes or ("none",)),
            ("Stable entrypoints", evidence.entrypoints),
            ("Manifests", evidence.manifests),
            ("Major structure", stable_structure[:45]),
            ("Supporting documentation", documentation)
        ),
        MAX_IDENTITY_EVIDENCE_BYTES
    )


def render_state_evidence(evidence):
    implementation_structure = tuple(
        path for path in evidence.structure
        if not path.startswith("tests/") and "/tests/" not in path
    )
    return _bounded_sections(
        "CURRENT IMPLEMENTATION EVIDENCE\n"
        "Derived deterministically from current project files. Prefer this section "
        "for claims about what currently exists.",
        (
            ("Current entrypoints", evidence.entrypoints),
            ("Current manifests and configuration", evidence.manifests),
            ("Observed Python classes and functions", evidence.implementation_facts[:14]),
            ("Observed literal CLI commands", evidence.cli_commands),
            ("Current package and module structure", implementation_structure[:60])
        ),
        MAX_STATE_EVIDENCE_BYTES
    )
