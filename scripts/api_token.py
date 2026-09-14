"""The API token the git hooks use, and the one place it comes from.

The token lives in the thinkube-control secret mcp-default-token. The hooks in
each application read a copy of it from a file, because a hook cannot be
handed a Kubernetes secret. A copy is only correct while it equals the secret,
so every writer here rewrites it when it differs, and the hooks refresh it
themselves when the platform rejects it.
"""

import os
from pathlib import Path

SECRET_NAME = 'mcp-default-token'
SECRET_NAMESPACE = 'thinkube-control'
TOKEN_FILE = Path('/home/thinkube/.thinkube/api-token')


def needs_writing(token_file: Path, secret_token: str) -> bool:
    """Whether the file must be written to equal the secret's token."""
    if not token_file.exists():
        return True
    return token_file.read_text() != secret_token


def write_token_file(token_file: Path, token: str) -> None:
    """Write the token readable by its owner only, from the moment it exists."""
    token_file.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(token_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as f:
        f.write(token)
    token_file.chmod(0o600)


# Shell functions shared by the generated pre-commit hook and
# regenerate-manifests.sh. Each script sets CONTROL_URL before sourcing these.
#
# load_api_token     the copy in the file, else THINKUBE_API_TOKEN.
# refresh_api_token  rewrites the file from the secret, when kubectl can read it.
# post_control PATH  POSTs to the control API; if the token is rejected it
#                    refreshes the token once and asks again. Prints the body
#                    followed by the HTTP status on its own last line.
TOKEN_SHELL = r'''
TOKEN_FILE="$HOME/.thinkube/api-token"

load_api_token() {
    if [ -f "$TOKEN_FILE" ]; then
        cat "$TOKEN_FILE"
    else
        printf '%s' "${THINKUBE_API_TOKEN:-}"
    fi
}

refresh_api_token() {
    command -v kubectl >/dev/null 2>&1 || return 1
    local fresh
    fresh=$(kubectl get secret mcp-default-token -n thinkube-control \
        -o jsonpath='{.data.token}' 2>/dev/null | base64 -d 2>/dev/null)
    [ -n "$fresh" ] || return 1
    # A redirect onto an existing file keeps that file's mode, so the token is
    # written to a new private file that then replaces the old one.
    local dir tmp
    dir=$(dirname "$TOKEN_FILE")
    mkdir -p "$dir" || return 1
    tmp=$(mktemp "$dir/.api-token.XXXXXX") || return 1
    chmod 600 "$tmp"
    printf '%s' "$fresh" > "$tmp"
    mv -f "$tmp" "$TOKEN_FILE"
    printf '%s' "$fresh"
}

post_control() {
    local path="$1" response code fresh
    response=$(curl -s -w "\n%{http_code}" -X POST \
        -H "Authorization: Bearer ${API_TOKEN}" \
        -H "Content-Type: application/json" \
        "${CONTROL_URL}${path}" 2>&1)
    code=$(printf '%s\n' "$response" | tail -1)
    if [ "$code" = "401" ] && fresh=$(refresh_api_token); then
        API_TOKEN="$fresh"
        response=$(curl -s -w "\n%{http_code}" -X POST \
            -H "Authorization: Bearer ${API_TOKEN}" \
            -H "Content-Type: application/json" \
            "${CONTROL_URL}${path}" 2>&1)
    fi
    printf '%s\n' "$response"
}

API_TOKEN=$(load_api_token)
if [ -z "$API_TOKEN" ]; then
    API_TOKEN=$(refresh_api_token) || API_TOKEN=""
fi
'''

# What a person is told when no token could be found or refreshed.
TOKEN_HELP = (
    'The API token is the secret mcp-default-token in the thinkube-control '
    'namespace. To copy it where the hooks read it:\n'
    "  kubectl get secret mcp-default-token -n thinkube-control "
    "-o jsonpath='{.data.token}' | base64 -d > ~/.thinkube/api-token"
)

# The help above as shell that prints it verbatim. A quoted heredoc expands
# nothing, so the braces and quotes of the command reach the terminal as written.
TOKEN_HELP_SHELL = "cat <<'TOKEN_HELP'\n" + TOKEN_HELP + "\nTOKEN_HELP\n"
