#!/bin/sh

# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

# run_tests.sh - Run the library's tests.
#
#   ./run_tests.sh              the whole suite
#   ./run_tests.sh <file>       one test file, and nothing else

set -e
cd "$(dirname "$0")"

pip install --break-system-packages -e ".[test]"

if [ -n "$1" ]; then
    exec python -m pytest "$1" -v
fi
python -m pytest tests/ -v
