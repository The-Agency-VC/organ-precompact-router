#!/bin/bash
# precompact-router.sh — Fleet-level PreCompact entry point.
# Single entry in ~/.claude/settings.json.
# Calls the local precompact-router.py with this directory as the registry dir.
set -u
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
EVENT=$(cat 2>/dev/null || echo '{}')
echo "$EVENT" | python "$SCRIPT_DIR/precompact-router.py" --registry-dir "$SCRIPT_DIR"
exit $?
