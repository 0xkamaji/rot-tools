import os

from rotbot.contexts import loader
from rotbot.contexts.paths import PathConfigurationError
from rotbot.ui.terminal import rot_say


CONTEXT_CATEGORIES = ("users", "assistants", "machines", "projects", "contacts")
PRIVACY_NAMESPACES = ("general", "private")


class PrivacyInspectionError(Exception):
    pass


def _filenames(directory):
    if not os.path.lexists(directory):
        return ()
    if directory.is_symlink() or not directory.is_dir():
        raise PrivacyInspectionError(f"Invalid context directory: {directory}")
    try:
        entries = tuple(directory.iterdir())
    except OSError as error:
        raise PrivacyInspectionError(f"Could not list context directory {directory}: {error}") from None
    files = []
    for entry in sorted(entries, key=lambda item: item.name):
        if entry.is_symlink() or not entry.is_file():
            raise PrivacyInspectionError(f"Invalid context document: {entry}")
        files.append(entry.name)
    return tuple(files)


def privacy_inspect(args):
    try:
        root = loader.CONTEXT_ROOT
        if root.exists() and (root.is_symlink() or not root.is_dir()):
            raise PrivacyInspectionError(f"Invalid context root: {root}")
        lines = [
            "ROTBOT PRIVACY INSPECTION",
            "-------------------------",
            f"Contexts root: {root}"
        ]
        for category_name in CONTEXT_CATEGORIES:
            category = root / category_name
            lines.extend(("", category_name.upper()))
            if not os.path.lexists(category):
                lines.append("(none)")
                continue
            if category.is_symlink() or not category.is_dir():
                raise PrivacyInspectionError(f"Invalid context category: {category}")
            try:
                entities = tuple(category.iterdir())
            except OSError as error:
                raise PrivacyInspectionError(
                    f"Could not list context category {category}: {error}"
                ) from None
            entities = tuple(
                sorted(
                    (entry for entry in entities if entry.name not in PRIVACY_NAMESPACES),
                    key=lambda item: item.name
                )
            )
            if not entities:
                lines.append("(none)")
                continue
            for entity in entities:
                if entity.is_symlink() or not entity.is_dir():
                    raise PrivacyInspectionError(f"Invalid context entry: {entity}")
                lines.append(entity.name)
                for namespace in PRIVACY_NAMESPACES:
                    filenames = _filenames(entity / namespace)
                    listed = ", ".join(filenames) if filenames else "(none)"
                    lines.append(f"  {namespace}: {listed}")
        lines.extend(("", "Machine-local config: excluded"))
    except (PathConfigurationError, PrivacyInspectionError, OSError) as error:
        rot_say(f"Could not inspect context privacy: {error}")
        return 2
    rot_say("\n".join(lines))
    return 0
