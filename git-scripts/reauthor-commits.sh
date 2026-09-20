#!/usr/bin/env bash
# Rewrites Git commit author and committer identity for commits matching an old email.

usage() {
    cat <<'EOF'
Usage:
  reauthor-commits.sh <old-email> <new-name> <new-email>

Arguments:
  old-email   Existing author/committer email to replace.
  new-name    Replacement author/committer name.
  new-email   Replacement author/committer email.

Options:
  -h, --help  Show this help message.
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
    usage
    exit 0
fi

if [[ $# -ne 3 ]]; then
    usage >&2
    exit 2
fi

export OLD_EMAIL="$1"
export CORRECT_NAME="$2"
export CORRECT_EMAIL="$3"

git filter-branch --env-filter '
if [ "$GIT_COMMITTER_EMAIL" = "$OLD_EMAIL" ]; then
    export GIT_COMMITTER_NAME="$CORRECT_NAME"
    export GIT_COMMITTER_EMAIL="$CORRECT_EMAIL"
fi

if [ "$GIT_AUTHOR_EMAIL" = "$OLD_EMAIL" ]; then
    export GIT_AUTHOR_NAME="$CORRECT_NAME"
    export GIT_AUTHOR_EMAIL="$CORRECT_EMAIL"
fi
' -f --tag-name-filter cat -- --branches --tags
