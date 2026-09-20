#!/usr/bin/env bash
# Lists commits whose author matches the provided name or email.

usage() {
    cat <<'EOF'
Usage:
  list-commits-by-author.sh <author>

Arguments:
  author      Author name or email pattern to match.

Options:
  -h, --help  Show this help message.
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
    usage
    exit 0
fi

if [[ $# -ne 1 ]]; then
    usage >&2
    exit 2
fi

AUTHOR="$1"

git log --author="$AUTHOR" --oneline
