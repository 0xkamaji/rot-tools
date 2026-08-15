import json
import os
from pathlib import Path
import tempfile
import tomllib
from typing import NamedTuple

ACCOUNTS_FILENAME = "accounts.toml"
VISIBILITIES = ("private", "public")


class AccountError(Exception):
    pass


class AccountFile(NamedTuple):
    git_name: str = ""
    git_email: str = ""
    github_username: str = ""
    github_default_visibility: str = ""


def _validated_name(value, label):
    if not isinstance(value, str) or not value.strip():
        raise AccountError(f"Invalid {label}: expected a non-empty string.")
    if any(
        (ord(character) < 32 or ord(character) == 127)
        for character in value
    ):
        raise AccountError(f"Invalid {label}: control characters are not allowed.")
    return value.strip()


def _validated_email(value, label):
    value = _validated_name(value, label)
    if "@" not in value:
        raise AccountError(f"Invalid {label}: expected an email address.")
    return value


def _parse_accounts(document):
    if not isinstance(document, dict):
        raise AccountError("accounts.toml must contain TOML tables.")
    git_entry = document.get("git")
    github_entry = document.get("github")
    git_name = ""
    git_email = ""
    if git_entry is not None:
        if not isinstance(git_entry, dict):
            raise AccountError("accounts.toml 'git' section must be a table.")
        if "name" in git_entry:
            git_name = _validated_name(git_entry["name"], "git.name")
        if "email" in git_entry:
            git_email = _validated_email(git_entry["email"], "git.email")
    github_username = ""
    visibility = ""
    if github_entry is not None:
        if not isinstance(github_entry, dict):
            raise AccountError("accounts.toml 'github' section must be a table.")
        if "username" in github_entry:
            github_username = _validated_name(
                github_entry["username"], "github.username"
            )
        if "default_visibility" in github_entry:
            selected = github_entry["default_visibility"]
            if not isinstance(selected, str) or selected not in VISIBILITIES:
                raise AccountError(
                    f"Invalid GitHub default visibility: {selected}. "
                    "Expected private or public."
                )
            visibility = selected
    return AccountFile(
        git_name, git_email, github_username, visibility
    )


def load_accounts(directory):
    path = Path(directory) / ACCOUNTS_FILENAME
    if not os.path.lexists(path):
        return None
    if path.is_symlink() or not path.is_file():
        raise AccountError(f"Invalid accounts file: {path}")
    try:
        content = path.read_text(encoding="utf-8")
        document = tomllib.loads(content)
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as error:
        raise AccountError(f"Could not load accounts file:\n{error}") from None
    return _parse_accounts(document)


def _validate_account(account):
    if not isinstance(account, AccountFile):
        return AccountFile(
            _validated_name(account.get("git_name", ""), "git.name") if account.get("git_name") else "",
            _validated_email(account.get("git_email", ""), "git.email") if account.get("git_email") else "",
            _validated_name(account.get("github_username", ""), "github.username") if account.get("github_username") else "",
            account.get("github_default_visibility", "")
        )
    git_name = _validated_name(account.git_name, "git.name") if account.git_name else ""
    git_email = _validated_email(account.git_email, "git.email") if account.git_email else ""
    github_username = (
        _validated_name(account.github_username, "github.username")
        if account.github_username else ""
    )
    visibility = account.github_default_visibility
    if visibility and visibility not in VISIBILITIES:
        raise AccountError(
            f"Invalid GitHub default visibility: {visibility}. "
            "Expected private or public."
        )
    return AccountFile(
        git_name, git_email, github_username, visibility
    )


def render_accounts(account):
    lines = []
    if account.git_name or account.git_email:
        lines.append("[git]")
        if account.git_name:
            lines.append(
                f"name = {json.dumps(account.git_name, ensure_ascii=False)}"
            )
        if account.git_email:
            lines.append(
                f"email = {json.dumps(account.git_email, ensure_ascii=False)}"
            )
    if account.github_username or account.github_default_visibility:
        if lines and lines[-1] != "":
            lines.append("")
        lines.append("[github]")
        if account.github_username:
            lines.append(
                f"username = {json.dumps(account.github_username, ensure_ascii=False)}"
            )
        if account.github_default_visibility:
            lines.append(
                "default_visibility = "
                f"{json.dumps(account.github_default_visibility, ensure_ascii=False)}"
            )
    return "\n".join(lines) + ("\n" if lines else "")


def write_accounts(directory, account):
    account = _validate_account(account)
    rendered = render_accounts(account)
    path = Path(directory) / ACCOUNTS_FILENAME
    temporary_path = None
    try:
        path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=path.name + ".",
            suffix=".tmp",
            delete=False
        ) as temporary:
            temporary_path = Path(temporary.name)
            os.chmod(temporary_path, 0o600)
            temporary.write(rendered)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, path)
        temporary_path = None
    except OSError as error:
        raise AccountError(f"Could not write accounts file:\n{path}\n{error}") from None
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except OSError:
                pass