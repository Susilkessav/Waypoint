#!/bin/sh
# Uses the same isolated recording helper documented in the README.
cd "$(dirname "$0")/.." || exit 1
exec uv run --locked python scripts/present.py "$@"
