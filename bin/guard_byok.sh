#!/usr/bin/env bash
# Wrapper forwarding to the canonical pre-commit hook.
exec "$(cd "$(dirname "$0")" && pwd)/hooks/pre-commit" "$@"
