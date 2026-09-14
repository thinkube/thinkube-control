"""The scripts written into each application to regenerate its k8s/ manifests.

Two scripts call the same control endpoint: the pre-commit hook, when
thinkube.yaml changes, and regenerate-manifests.sh, on demand. Both read the
API token through the shell in api_token.TOKEN_SHELL, and both stop with the
same instructions when it is missing or rejected.

The bodies are raw strings so that shell syntax is written as the shell reads
it; only the three values below are substituted.
"""

from api_token import TOKEN_HELP_SHELL, TOKEN_SHELL

REGENERATE_PATH = '/api/v1/templates/apps/${APP_NAME}/regenerate-manifests'


def _values(app_name: str, domain: str, control_url: str) -> str:
    return (
        f'APP_NAME="{app_name}"\n'
        f'DOMAIN_NAME="{domain}"\n'
        f'CONTROL_URL="{control_url}"\n'
    )


def pre_commit_hook(app_name: str, domain: str, control_url: str) -> str:
    return (
        r'''#!/bin/bash
# Git pre-commit hook to regenerate k8s/ manifests when thinkube.yaml changes
# AUTO-GENERATED - DO NOT EDIT

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

'''
        + _values(app_name, domain, control_url)
        + r'''
# Check if thinkube.yaml has been modified in this commit
if ! git diff --cached --name-only | grep -q '^thinkube\.yaml$'; then
    exit 0
fi

echo -e "${YELLOW}Pre-commit hook: thinkube.yaml changed, regenerating k8s/ manifests...${NC}"
'''
        + TOKEN_SHELL
        + r'''
if [ -z "$API_TOKEN" ]; then
    echo -e "${RED}ERROR: No API token at $TOKEN_FILE.${NC}"
'''
        + TOKEN_HELP_SHELL
        + r'''    exit 1
fi

RESPONSE=$(post_control "''' + REGENERATE_PATH + r'''")
HTTP_CODE=$(printf '%s\n' "$RESPONSE" | tail -1)
BODY=$(printf '%s\n' "$RESPONSE" | sed '$d')

if [ "$HTTP_CODE" != "200" ]; then
    echo -e "${RED}ERROR: Failed to regenerate manifests (HTTP ${HTTP_CODE})${NC}"
    echo "$BODY"
    if [ "$HTTP_CODE" = "401" ]; then
        echo ""
'''
        + TOKEN_HELP_SHELL
        + r'''    fi
    echo ""
    echo "You can still commit without manifest regeneration by using:"
    echo "  git commit --no-verify"
    exit 1
fi

# Stage the regenerated k8s/ files
if [ -d "k8s" ]; then
    git add k8s/
    echo -e "${GREEN}k8s/ manifests regenerated and staged for commit.${NC}"
fi

exit 0
'''
    )


def regenerate_script(app_name: str, domain: str, control_url: str) -> str:
    return (
        r'''#!/bin/bash
# Manually regenerate k8s/ manifests from thinkube.yaml
# AUTO-GENERATED - DO NOT EDIT

'''
        + _values(app_name, domain, control_url)
        + TOKEN_SHELL
        + r'''
if [ -z "$API_TOKEN" ]; then
    echo "ERROR: No API token at $TOKEN_FILE."
'''
        + TOKEN_HELP_SHELL
        + r'''    exit 1
fi

echo "Regenerating k8s/ manifests for ${APP_NAME}..."

RESPONSE=$(post_control "''' + REGENERATE_PATH + r'''")
HTTP_CODE=$(printf '%s\n' "$RESPONSE" | tail -1)
BODY=$(printf '%s\n' "$RESPONSE" | sed '$d')

if [ "$HTTP_CODE" = "200" ]; then
    echo "Manifests regenerated successfully."
    echo "$BODY" | python3 -c "import sys,json; d=json.load(sys.stdin); print('Files: ' + ', '.join(d['files_generated']))" 2>/dev/null || true
else
    echo "ERROR: Failed (HTTP ${HTTP_CODE})"
    echo "$BODY"
    if [ "$HTTP_CODE" = "401" ]; then
        echo ""
'''
        + TOKEN_HELP_SHELL
        + r'''    fi
    exit 1
fi
'''
    )
