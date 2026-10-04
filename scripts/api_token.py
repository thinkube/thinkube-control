# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""The API token the git hooks use, and the one place it comes from.

The token lives in the thinkube-control secret mcp-default-token. The hooks in
each application read a copy of it from one file, because a hook cannot be
handed a Kubernetes secret. The deploy writes that file whenever it differs
from the secret. The hooks read the file and nothing else: when it is missing
or the platform rejects it, they stop and say how to put it right.
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


# Shell shared by the generated pre-commit hook and regenerate-manifests.sh.
# Each script sets CONTROL_URL before this.
#
# API_TOKEN          the contents of TOKEN_FILE, empty when the file is missing.
# post_control PATH  POSTs to the control API. Prints the body followed by the
#                    HTTP status on its own last line.
TOKEN_SHELL = r'''
TOKEN_FILE="$HOME/.thinkube/api-token"

post_control() {
    local path="$1" response
    response=$(curl -s -w "\n%{http_code}" -X POST \
        -H "Authorization: Bearer ${API_TOKEN}" \
        -H "Content-Type: application/json" \
        "${CONTROL_URL}${path}" 2>&1)
    printf '%s\n' "$response"
}

API_TOKEN=""
if [ -f "$TOKEN_FILE" ]; then
    API_TOKEN=$(cat "$TOKEN_FILE")
fi
'''

# What a person is told when the token file is missing or rejected.
TOKEN_HELP = (
    'The API token is the secret mcp-default-token in the thinkube-control '
    'namespace. To copy it where the hooks read it:\n'
    "  kubectl get secret mcp-default-token -n thinkube-control "
    "-o jsonpath='{.data.token}' | base64 -d > ~/.thinkube/api-token"
)

# The help above as shell that prints it verbatim. A quoted heredoc expands
# nothing, so the braces and quotes of the command reach the terminal as written.
TOKEN_HELP_SHELL = "cat <<'TOKEN_HELP'\n" + TOKEN_HELP + "\nTOKEN_HELP\n"
