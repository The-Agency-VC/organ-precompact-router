#!/usr/bin/env python3
"""
agent-register.py — Self-registration helper for agent install scripts.

Usage:
  python agent-register.py \\
    --registry /path/to/agent-registry.json \\
    --slug my-agent \\
    --pattern data/agents/my-agent \\
    --hook /path/to/hook.sh \\          # leaf: actually intercepts compaction
    --root /path/to/agent/root \\
    [--sub-router /path/to/router.sh]   # branch: delegate to next level down
    [--remove]                           # deregister

Exactly one of --hook or --sub-router must be provided (not both).
Idempotent: re-running updates an existing entry without duplicating.
"""

import sys, json, argparse
from pathlib import Path
from datetime import datetime, timezone

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--registry", required=True)
    p.add_argument("--slug", required=True)
    p.add_argument("--pattern", required=True)
    p.add_argument("--hook", default=None)
    p.add_argument("--sub-router", dest="sub_router", default=None)
    p.add_argument("--root", default=None)
    p.add_argument("--remove", action="store_true")
    args = p.parse_args()

    if not args.remove and not args.hook and not args.sub_router:
        print("[register] ERROR: must provide --hook or --sub-router", file=sys.stderr)
        sys.exit(1)

    registry_path = Path(args.registry)

    if registry_path.is_file():
        try:
            registry = json.loads(registry_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            registry = {}
    else:
        registry = {}

    registry.setdefault("version", "1")
    registry.setdefault("agents", [])
    agents = registry["agents"]

    before = len(agents)
    agents = [a for a in agents if a.get("slug") != args.slug]
    removed = before - len(agents)

    if args.remove:
        registry["agents"] = agents
        registry_path.write_text(json.dumps(registry, indent=2) + "\n", encoding="utf-8")
        print(f"[register] deregistered {args.slug!r} ({removed} removed); registry={registry_path}")
        return

    entry = {
        "slug": args.slug,
        "owner_pattern": args.pattern,
        "registered_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    if args.sub_router:
        entry["sub_router"] = args.sub_router
    if args.hook:
        entry["hook"] = args.hook
    if args.root:
        entry["agent_root"] = args.root

    agents.append(entry)
    registry["agents"] = agents
    registry_path.write_text(json.dumps(registry, indent=2) + "\n", encoding="utf-8")

    kind = "sub_router" if args.sub_router else "hook"
    print(f"[register] registered {args.slug!r} as {kind} (pattern={args.pattern!r}); registry={registry_path}")

if __name__ == "__main__":
    main()
