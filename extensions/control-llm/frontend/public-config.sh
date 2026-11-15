#!/bin/sh

# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

# Publish the variables named in PUBLIC_ENV_VARS to the browser.
#
# The bundle is built before the deployment exists, so the values it needs
# arrive here, in the container's environment, and are written to a script
# the page loads before its own code.
#
# PUBLIC_ENV_VARS is an allow-list of names, set by the platform from this
# container's publicEnv in thinkube.yaml. The container holds the whole
# environment, secrets included, so a name that is not on the list never
# reaches the page.
#
# nginx runs every executable script in /docker-entrypoint.d before it starts.

set -e

CONFIG_FILE="${CONFIG_FILE:-/usr/share/nginx/html/config.js}"

# No colon in the expansion: an empty PUBLIC_ENV_VARS means publish nothing,
# while an unset one falls back for platforms that do not set it yet.
NAMES="${PUBLIC_ENV_VARS-APP_TITLE}"

escape() {
    printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g' | tr -d '\n\r'
}

{
    printf 'window.__PUBLIC_CONFIG__ = {'
    first=1
    for name in $(printf '%s' "$NAMES" | tr ',' ' '); do
        value=$(printenv "$name" || true)
        [ -n "$value" ] || continue
        [ "$first" -eq 1 ] || printf ','
        printf '"%s":"%s"' "$name" "$(escape "$value")"
        first=0
    done
    printf '};\n'
} > "$CONFIG_FILE"

echo "public config: ${NAMES:-none}"
