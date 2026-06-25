#!/bin/bash
# organ-precompact-router install.sh
#
# Two modes:
#   --fleet  FLEET_ROOT=/path/to/fleet-root
#            Installs router at fleet level + wires settings.json.
#            Run once per machine.
#
#   --body   BODY_ROOT=/path/to/agent
#            Installs router into a body's runtime/ (for sub-routing).
#            Run once per agent body that hosts children.
#
# Both modes: copy precompact-router.py, precompact-router.sh, agent-register.py
# into the target runtime/ directory and create an empty agent-registry.json.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
MODE="${1:-}"

if [ "$MODE" = "--fleet" ]; then
  TARGET="${FLEET_ROOT:?FLEET_ROOT must be set for --fleet mode}/runtime"
elif [ "$MODE" = "--body" ]; then
  TARGET="${BODY_ROOT:?BODY_ROOT must be set for --body mode}/runtime"
else
  echo "Usage: $0 --fleet (FLEET_ROOT=...) | --body (BODY_ROOT=...)" >&2
  exit 1
fi

mkdir -p "$TARGET"

cp "$SCRIPT_DIR/precompact-router.py"  "$TARGET/precompact-router.py"
cp "$SCRIPT_DIR/agent-register.py"     "$TARGET/agent-register.py"

# Write local router.sh that calls the local router.py
cat > "$TARGET/precompact-router.sh" <<'SH'
#!/bin/bash
set -u
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
EVENT=$(cat 2>/dev/null || echo '{}')
echo "$EVENT" | python "$SCRIPT_DIR/precompact-router.py" --registry-dir "$SCRIPT_DIR"
exit $?
SH
chmod +x "$TARGET/precompact-router.sh"

# Create empty registry if not present
REGISTRY="$TARGET/agent-registry.json"
if [ ! -f "$REGISTRY" ]; then
  python - "$REGISTRY" <<'PY'
import json, sys
path = sys.argv[1]
with open(path, 'w') as f:
    json.dump({"version": "1", "agents": []}, f, indent=2)
    f.write('\n')
print(f"[router-install] created empty registry: {path}")
PY
fi

echo "[router-install] installed to $TARGET"

# Fleet mode: wire settings.json
if [ "$MODE" = "--fleet" ]; then
  ROUTER_CMD="bash '$TARGET/precompact-router.sh'"
  SETTINGS="$HOME/.claude/settings.json"
  python - "$SETTINGS" "$ROUTER_CMD" <<'PY'
import json, sys, pathlib
settings_path = pathlib.Path(sys.argv[1])
router_cmd = sys.argv[2]
try:
    data = json.loads(settings_path.read_text(encoding="utf-8") or "{}")
except Exception:
    data = {}
hooks = data.setdefault("hooks", {})
precompact = hooks.setdefault("PreCompact", [])
# Idempotent: remove any existing router entry
precompact = [g for g in precompact if not any(
    isinstance(h, dict) and "precompact-router" in str(h.get("command",""))
    for h in g.get("hooks", [])
)]
precompact.append({"matcher": "", "hooks": [{"type": "command", "command": router_cmd, "timeout": 90}]})
hooks["PreCompact"] = precompact
data["hooks"] = hooks
settings_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
print(f"[router-install] wired settings.json: {settings_path}")
PY
fi
