from pathlib import Path, PurePosixPath
import json
import re
import subprocess
import tomllib
from typing import NamedTuple
from urllib.parse import urlsplit

from rotbot.contexts import loader


CADDY_CONFIG_PATHS = (
    Path("/etc/caddy/Caddyfile"),
    Path("/usr/local/etc/caddy/Caddyfile")
)
DOMAIN_PATTERN = re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$"
)


class MatchError(Exception):
    pass


class MatchSection(NamedTuple):
    is_git_repo: bool | None
    git_remotes: tuple
    domains: tuple
    required_paths: tuple
    optional_paths: tuple


class MatchDefinition(NamedTuple):
    source: object
    production: object


class Evidence(NamedTuple):
    passed: bool
    message: str


class MatchCandidate(NamedTuple):
    name: str
    binding_type: str
    path: Path
    strong: bool
    evidence: tuple


def normalize_git_remote(remote):
    value = remote.strip()
    if not value:
        return None

    scp_match = re.fullmatch(r"(?:[^@/:]+@)?([^/:]+):(.+)", value)
    if scp_match and "://" not in value:
        host = scp_match.group(1).lower()
        path = scp_match.group(2)
        authority = host
    else:
        try:
            parsed = urlsplit(value if "://" in value else f"//{value}")
            hostname = parsed.hostname
        except ValueError:
            return None
        if parsed.scheme and parsed.scheme not in {"http", "https", "ssh"}:
            return None
        if parsed.query or parsed.fragment or not hostname:
            return None
        if parsed.scheme in {"http", "https"} and (parsed.username or parsed.password):
            return None
        host = hostname.lower()
        try:
            port = parsed.port
        except ValueError:
            return None
        defaults = {"http": 80, "https": 443, "ssh": 22}
        authority = host if not port or port == defaults.get(parsed.scheme) else f"{host}:{port}"
        path = parsed.path

    path = path.strip("/")
    if path.endswith(".git"):
        path = path[:-4]
    parts = path.split("/")
    if len(parts) < 2 or any(part in {"", ".", ".."} for part in parts):
        return None
    return f"{authority}/{'/'.join(parts)}"


def _normalize_domain(domain):
    value = domain.strip().rstrip(".").lower()
    if not DOMAIN_PATTERN.fullmatch(value):
        raise MatchError(f"Invalid domain in match definition: {domain}")
    return value


def _validate_required_path(value):
    if not value or "\\" in value or "\0" in value or any(
        character in value for character in "*?[]"
    ):
        raise MatchError(f"Invalid required path in match definition: {value}")
    path = PurePosixPath(value.rstrip("/"))
    if path.is_absolute() or str(path) in {"", "."} or ".." in path.parts:
        raise MatchError(f"Invalid required path in match definition: {value}")
    return str(path) + ("/" if value.endswith("/") else "")


def parse_legacy_match_document(markdown):
    section = None
    label = None
    seen_heading = False
    values = {
        "Source": {"Git remotes": [], "Required paths": []},
        "Production": {"Domains": [], "Required paths": []}
    }
    seen_sections = set()
    seen_labels = set()

    for raw_line in markdown.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if not seen_heading:
            if line != "# Match":
                raise MatchError("Match document must begin with '# Match'.")
            seen_heading = True
            continue
        if line.startswith("## "):
            section = line[3:]
            if section not in values or section in seen_sections:
                raise MatchError(f"Unsupported or duplicate match section: {section}")
            seen_sections.add(section)
            label = None
            continue
        if line.endswith(":") and not line.startswith("-"):
            if section is None:
                raise MatchError("Match list heading appears outside a section.")
            label = line[:-1]
            if label not in values[section]:
                raise MatchError(f"Unsupported match list: {line}")
            label_key = (section, label)
            if label_key in seen_labels:
                raise MatchError(f"Duplicate match list: {line}")
            seen_labels.add(label_key)
            continue
        if line.startswith("- "):
            if section is None or label is None:
                raise MatchError("Match item appears outside a recognized list.")
            item = line[2:].strip()
            if not item:
                raise MatchError("Match list items must not be empty.")
            values[section][label].append(item)
            continue
        raise MatchError(f"Unsupported match document content: {line}")

    if not seen_heading or not seen_sections:
        raise MatchError("Match document must define Source or Production.")

    source = None
    if "Source" in seen_sections:
        remotes = values["Source"]["Git remotes"]
        paths = values["Source"]["Required paths"]
        if not remotes or not paths:
            raise MatchError("Source match requires Git remotes and Required paths.")
        normalized_remotes = []
        for remote in remotes:
            normalized = normalize_git_remote(remote)
            if normalized is None:
                raise MatchError(f"Invalid Git remote in match definition: {remote}")
            normalized_remotes.append(normalized)
        source = MatchSection(
            True,
            tuple(normalized_remotes),
            (),
            tuple(_validate_required_path(path) for path in paths),
            ()
        )

    production = None
    if "Production" in seen_sections:
        domains = values["Production"]["Domains"]
        paths = values["Production"]["Required paths"]
        if not domains or not paths:
            raise MatchError("Production match requires Domains and Required paths.")
        production = MatchSection(
            None,
            (),
            tuple(_normalize_domain(domain) for domain in domains),
            tuple(_validate_required_path(path) for path in paths),
            ()
        )

    return MatchDefinition(source, production)


def _string_list(table, key):
    values = table.get(key, [])
    if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
        raise MatchError(f"Match field must be an array of strings: {key}")
    return tuple(values)


def parse_match_toml(content):
    try:
        document = tomllib.loads(content)
    except tomllib.TOMLDecodeError as error:
        raise MatchError(f"Invalid match TOML: {error}") from None
    if not document or set(document) - {"source", "production"}:
        raise MatchError("Match TOML must define only source or production tables.")

    source = None
    if "source" in document:
        table = document["source"]
        allowed = {"is_git_repo", "git_remotes", "required_paths", "optional_paths"}
        if not isinstance(table, dict) or set(table) - allowed:
            raise MatchError("Source match contains unsupported fields.")
        is_git_repo = table.get("is_git_repo")
        if not isinstance(is_git_repo, bool):
            raise MatchError("Source match requires a boolean is_git_repo field.")
        required = _string_list(table, "required_paths")
        optional = _string_list(table, "optional_paths")
        remotes = _string_list(table, "git_remotes")
        if not required:
            raise MatchError("Source match requires at least one required path.")
        if remotes and not is_git_repo:
            raise MatchError("A non-Git source match cannot declare Git remotes.")
        normalized_remotes = []
        for remote in remotes:
            normalized = normalize_git_remote(remote)
            if normalized is None:
                raise MatchError(f"Invalid Git remote in match definition: {remote}")
            normalized_remotes.append(normalized)
        source = MatchSection(
            is_git_repo,
            tuple(normalized_remotes),
            (),
            tuple(_validate_required_path(path) for path in required),
            tuple(_validate_required_path(path) for path in optional)
        )

    production = None
    if "production" in document:
        table = document["production"]
        allowed = {"domains", "required_paths", "optional_paths"}
        if not isinstance(table, dict) or set(table) - allowed:
            raise MatchError("Production match contains unsupported fields.")
        domains = _string_list(table, "domains")
        required = _string_list(table, "required_paths")
        optional = _string_list(table, "optional_paths")
        if not domains or not required:
            raise MatchError("Production match requires domains and required paths.")
        production = MatchSection(
            None,
            (),
            tuple(_normalize_domain(domain) for domain in domains),
            tuple(_validate_required_path(path) for path in required),
            tuple(_validate_required_path(path) for path in optional)
        )
    return MatchDefinition(source, production)


def _toml_array(values):
    return "[\n" + "".join(f"    {json.dumps(value)},\n" for value in values) + "]"


def render_match_toml(definition):
    sections = []
    if definition.source is not None:
        source = definition.source
        lines = [
            "[source]",
            f"is_git_repo = {'true' if source.is_git_repo else 'false'}",
            f"required_paths = {_toml_array(source.required_paths)}"
        ]
        if source.optional_paths:
            lines.append(f"optional_paths = {_toml_array(source.optional_paths)}")
        if source.git_remotes:
            lines.append(f"git_remotes = {_toml_array(source.git_remotes)}")
        sections.append("\n".join(lines))
    if definition.production is not None:
        production = definition.production
        lines = [
            "[production]",
            f"domains = {_toml_array(production.domains)}",
            f"required_paths = {_toml_array(production.required_paths)}"
        ]
        if production.optional_paths:
            lines.append(f"optional_paths = {_toml_array(production.optional_paths)}")
        sections.append("\n".join(lines))
    content = "\n\n".join(sections) + "\n"
    parse_match_toml(content)
    return content


def load_match_definition(name):
    try:
        identity_path, _state_path = loader.context_paths(name)
        directory = identity_path.parent
        if directory.name in {"local", "shareable"}:
            directory = directory.parent
        match_path = loader._existing_project_document(directory, "match.toml")
        legacy_path = loader._existing_project_document(directory, "match.md")
    except loader.ContextError as error:
        raise MatchError(str(error)) from None
    if match_path.is_symlink() or legacy_path.is_symlink():
        raise MatchError(f"Invalid match document for context: {name}")
    if not match_path.exists():
        match_path = legacy_path
        if not match_path.exists():
            return None
    if not match_path.is_file():
        raise MatchError(f"Invalid match document for context: {name}")
    try:
        content = match_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise MatchError(f"Could not load match document for '{name}': {error}") from None
    try:
        return (
            parse_match_toml(content)
            if match_path.name == "match.toml"
            else parse_legacy_match_document(content)
        )
    except MatchError as error:
        raise MatchError(f"Invalid match document for '{name}': {error}") from None


def discover_match_definitions(names=None):
    context_names = loader.list_contexts() if names is None else tuple(names)
    definitions = []
    for name in context_names:
        try:
            definition = load_match_definition(name)
        except loader.ContextError as error:
            raise MatchError(str(error)) from None
        if definition is not None:
            definitions.append((name, definition))
    return tuple(definitions)


def _required_path_evidence(root, required_paths):
    evidence = []
    all_found = True
    for relative in required_paths:
        candidate = root / relative.rstrip("/")
        current = root
        safe = True
        for part in PurePosixPath(relative.rstrip("/")).parts:
            current = current / part
            if current.is_symlink():
                safe = False
                break
        found = safe and (
            candidate.is_dir()
            if relative.endswith("/")
            else candidate.is_file()
        )
        all_found = all_found and found
        evidence.append(Evidence(found, f"Found {relative}" if found else f"Missing {relative}"))
    return all_found, tuple(evidence)


def _optional_path_evidence(root, optional_paths):
    return _required_path_evidence(root, optional_paths)[1]


def _git_details(candidate):
    try:
        root_result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=candidate,
            capture_output=True,
            text=True,
            check=False
        )
    except FileNotFoundError:
        return None, (), "Git is not available."
    if root_result.returncode != 0:
        return None, (), "Candidate is not a Git repository."
    root = Path(root_result.stdout.strip()).resolve()
    remotes_result = subprocess.run(
        ["git", "remote"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False
    )
    remotes = []
    if remotes_result.returncode == 0:
        for remote_name in sorted(remotes_result.stdout.splitlines()):
            for mode in ((), ("--push",)):
                urls = subprocess.run(
                    ["git", "remote", "get-url", *mode, "--all", remote_name],
                    cwd=root,
                    capture_output=True,
                    text=True,
                    check=False
                )
                if urls.returncode == 0:
                    for url in urls.stdout.splitlines():
                        normalized = normalize_git_remote(url)
                        item = (remote_name, url, normalized)
                        if normalized and item not in remotes:
                            remotes.append(item)
    return root, tuple(remotes), None


def _source_candidate(name, definition, candidate, git_details):
    git_root, remotes, git_error = git_details
    evidence = []
    is_git_repo = git_root == candidate
    git_matches = is_git_repo == definition.is_git_repo
    if definition.is_git_repo:
        evidence.append(Evidence(git_matches, (
            f"Git root resolves to {git_root}"
            if is_git_repo else git_error or "Candidate is not a Git repository root."
        )))
    else:
        evidence.append(Evidence(
            git_matches,
            "Candidate is not a Git repository."
            if git_matches else f"Candidate is a Git repository rooted at {git_root}."
        ))

    remote_matches = True
    if definition.git_remotes:
        configured = set(definition.git_remotes)
        matching = [remote for remote in remotes if remote[2] in configured]
        remote_matches = bool(matching)
        if matching:
            for remote_name, _url, normalized in matching:
                evidence.append(Evidence(True, f"Git remote {remote_name} matches {normalized}"))
        else:
            evidence.append(Evidence(False, "No configured Git remote matches."))

    paths_ok, path_evidence = _required_path_evidence(candidate, definition.required_paths)
    evidence.extend(path_evidence)
    evidence.extend(_optional_path_evidence(candidate, definition.optional_paths))
    strong = git_matches and remote_matches and paths_ok
    return MatchCandidate(name, "source", candidate, strong, tuple(evidence))


def inspect_source_project(path):
    candidate = Path(path).expanduser()
    if not candidate.exists() or not candidate.is_dir():
        raise MatchError(f"Source path is not a directory:\n{candidate}")
    candidate = candidate.resolve()
    git_root, remotes, _git_error = _git_details(candidate)
    is_git_repo = git_root == candidate
    normalized = tuple(sorted({remote[2] for remote in remotes})) if is_git_repo else ()
    return candidate, is_git_repo, normalized


def build_source_match_toml(is_git_repo, required_paths, optional_paths=(), git_remotes=()):
    definition = MatchDefinition(
        MatchSection(
            bool(is_git_repo),
            tuple(git_remotes),
            (),
            tuple(required_paths),
            tuple(optional_paths)
        ),
        None
    )
    return render_match_toml(definition)


def match_source_definition(path, name, definition):
    candidate = Path(path).expanduser()
    if not candidate.exists() or not candidate.is_dir():
        raise MatchError(f"Source path is not a directory:\n{candidate}")
    candidate = candidate.resolve()
    if definition.source is None or definition.production is not None:
        raise MatchError("Generated match definition must contain only Source.")
    return _source_candidate(name, definition.source, candidate, _git_details(candidate))


def _caddy_sites(paths):
    sites = []
    available = False
    errors = []
    for config_path in paths:
        if not config_path.exists():
            continue
        available = True
        if config_path.is_symlink():
            errors.append(f"Caddy configuration is a symlink: {config_path}")
            continue
        try:
            lines = config_path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError) as error:
            errors.append(f"Could not read Caddy configuration {config_path}: {error}")
            continue

        index = 0
        while index < len(lines):
            stripped = lines[index].split("#", 1)[0].strip()
            if not stripped.endswith("{"):
                index += 1
                continue
            addresses = stripped[:-1].strip()
            block = []
            depth = 1
            index += 1
            while index < len(lines) and depth:
                line = lines[index].split("#", 1)[0].strip()
                depth += line.count("{") - line.count("}")
                if depth:
                    block.append(line)
                index += 1
            if depth != 0 or any("{" in line for line in block):
                continue
            roots = []
            file_server = False
            for line in block:
                root_match = re.fullmatch(r"root(?:\s+\*)?\s+(\S+)", line)
                if root_match and Path(root_match.group(1)).is_absolute():
                    roots.append(Path(root_match.group(1)).resolve())
                if line == "file_server":
                    file_server = True
            if len(set(roots)) != 1 or not file_server:
                continue
            for address in re.split(r"[\s,]+", addresses):
                if not address:
                    continue
                try:
                    parsed = urlsplit(address if "://" in address else f"//{address}")
                    hostname = parsed.hostname
                except ValueError:
                    continue
                if hostname:
                    try:
                        domain = _normalize_domain(hostname)
                    except MatchError:
                        continue
                    sites.append((domain, roots[0], config_path))
    if not available:
        errors.append("Caddy configuration is unavailable.")
    return tuple(sites), tuple(errors)


def _production_candidate(name, definition, candidate, caddy_details):
    sites, caddy_errors = caddy_details
    evidence = [Evidence(False, error) for error in caddy_errors]
    matches = [site for site in sites if site[0] in definition.domains]
    configured_roots = {site[1] for site in matches}
    root_matches = [
        site
        for site in matches
        if len(configured_roots) == 1 and site[1] == candidate
    ]
    if matches:
        for domain, root, _config in matches:
            evidence.append(Evidence(True, f"Caddy configures {domain} with root {root}"))
    else:
        evidence.append(Evidence(False, "No declared domain appears in Caddy configuration."))
    if matches and not root_matches:
        evidence.append(Evidence(False, "Configured Caddy root does not match candidate."))

    paths_ok, path_evidence = _required_path_evidence(candidate, definition.required_paths)
    evidence.extend(path_evidence)
    evidence.extend(_optional_path_evidence(candidate, definition.optional_paths))
    strong = bool(root_matches) and paths_ok
    return MatchCandidate(name, "production", candidate, strong, tuple(evidence))


def match_contexts(path, name=None, binding_type=None, caddy_paths=None):
    candidate = Path(path).expanduser()
    if not candidate.exists() or not candidate.is_dir():
        raise MatchError(f"Context binding path is not a directory:\n{candidate}")
    candidate = candidate.resolve()
    if binding_type not in {None, "source", "production"}:
        raise MatchError(f"Invalid binding type: {binding_type}")

    definitions = discover_match_definitions((name,)) if name else discover_match_definitions()
    if name and not definitions:
        raise MatchError(f"Context '{name}' does not have a match.toml definition.")
    if name and binding_type:
        definition = definitions[0][1]
        if getattr(definition, binding_type) is None:
            raise MatchError(
                f"Context '{name}' does not define a {binding_type} match."
            )
    needs_source = any(
        definition.source is not None and binding_type in {None, "source"}
        for _context_name, definition in definitions
    )
    needs_production = any(
        definition.production is not None and binding_type in {None, "production"}
        for _context_name, definition in definitions
    )
    git_details = _git_details(candidate) if needs_source else (None, (), None)
    caddy_details = (
        _caddy_sites(
            CADDY_CONFIG_PATHS
            if caddy_paths is None
            else tuple(Path(path) for path in caddy_paths)
        )
        if needs_production
        else ((), ())
    )
    candidates = []
    for context_name, definition in definitions:
        if definition.source is not None and binding_type in {None, "source"}:
            candidates.append(
                _source_candidate(context_name, definition.source, candidate, git_details)
            )
        if definition.production is not None and binding_type in {None, "production"}:
            candidates.append(
                _production_candidate(
                    context_name,
                    definition.production,
                    candidate,
                    caddy_details
                )
            )
    return tuple(candidates)
