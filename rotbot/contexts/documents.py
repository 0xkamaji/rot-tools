from pathlib import Path


PRIVACY_NAMESPACES = ("local", "shareable")


class ContextDocumentError(Exception):
    pass


def populated_markdown_sections(markdown, filename):
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
        line = raw_line
        output = ""
        while line:
            if in_comment:
                end = line.find("-->")
                if end < 0:
                    line = ""
                    break
                line = line[end + 3:]
                in_comment = False
                continue
            start = line.find("<!--")
            if start < 0:
                output += line
                break
            output += line[:start]
            line = line[start + 4:]
            in_comment = True
        stripped = output.strip()
        if fence is not None:
            body.append(output)
            if stripped.startswith(fence):
                fence = None
            continue
        if stripped.startswith("```") or stripped.startswith("~~~"):
            fence = stripped[:3]
            body.append(output)
            continue
        if output.startswith("## "):
            finish_section()
            heading = output[3:].strip()
            body = []
            continue
        if heading is None and output.startswith("# ") and not "".join(body).strip():
            continue
        body.append(output)
    if in_comment:
        raise ContextDocumentError(f"Unterminated Markdown comment: {filename}")
    if fence is not None:
        raise ContextDocumentError(f"Unterminated Markdown fence: {filename}")
    finish_section()
    return tuple(sections)


def namespace_files(directory, namespace, allowed=None):
    if namespace not in PRIVACY_NAMESPACES:
        raise ContextDocumentError(f"Unknown context privacy namespace: {namespace}")
    root = Path(directory) / namespace
    if not root.exists():
        return ()
    if root.is_symlink() or not root.is_dir():
        raise ContextDocumentError(f"Invalid context privacy namespace: {root}")
    files = []
    for path in sorted(root.iterdir(), key=lambda item: item.name):
        if path.is_symlink() or not path.is_file():
            raise ContextDocumentError(f"Invalid context document: {path}")
        if allowed is not None and path.name not in allowed:
            raise ContextDocumentError(f"Unsupported context document: {path.name}")
        files.append(path)
    return tuple(files)


def semantic_files(directory, view, allowed=None, include_legacy_local=True):
    if view == "egress":
        return namespace_files(directory, "shareable", allowed)
    if view != "full":
        raise ContextDocumentError(f"Unknown context view: {view}")
    files = [
        *namespace_files(directory, "shareable", allowed),
        *namespace_files(directory, "local", allowed)
    ]
    if include_legacy_local:
        for path in sorted(Path(directory).iterdir(), key=lambda item: item.name):
            if path.name == "metadata.toml" or path.name in PRIVACY_NAMESPACES:
                continue
            if path.is_symlink() or not path.is_file():
                continue
            if allowed is None or path.name in allowed:
                files.append(path)
    return tuple(files)


def privacy_inventory(directory):
    return {
        namespace: tuple(path.name for path in namespace_files(directory, namespace))
        for namespace in PRIVACY_NAMESPACES
    }
