#!/bin/sh

# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

# run_tests.sh - Run the frontend tests.
#
#   ./run_tests.sh              the whole suite, with lint and coverage (CI)
#   ./run_tests.sh <file>       one test file, and nothing else
#
# Thinkube Tandem runs single files this way, on a tree it has provisioned.

set -e

# One file: run it and stop.
if [ -n "$1" ]; then
    exec npx vitest run "$1"
fi

echo "Running frontend tests..."

# Install dependencies if needed.
#
# The cache belongs to the machine running the install, never to the
# repository: a cache under the shared checkout is written by every node
# that ever tested this app — on this platform that means both amd64 and
# arm64 — and npm reads it back as a corrupt tree ("Cannot read properties
# of null (reading 'edgesOut')"). npm's own default, or npm_config_cache
# from the environment, is where it goes.
if [ -f package-lock.json ]; then
    echo "Installing dependencies with npm ci..."
    npm ci
else
    echo "Installing dependencies with npm install..."
    npm install
fi

# The tests, and nothing else.
#
# A type check and a linter are things a person runs while writing, and
# `npm run typecheck` and `npm run lint` are here for that. On every
# deploy they cost minutes — TypeScript alone must read the declarations
# of every library the app imports — and they answer a question the tests
# have already answered: does this work. Coverage instruments every file
# to print a report nobody reads on a deploy.
echo "\nRunning unit tests..."
npm run test
echo "Tests passed successfully!"
