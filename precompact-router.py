#!/usr/bin/env python3
"""
precompact-router.py — Holographic PreCompact dispatcher.

Place this script (or a router.sh that calls it with --registry-dir) in any
body's runtime/ directory. It reads that body's local agent-registry.json and
dispatches to the matching agent's hook or sub-router.

The same script runs at every level of the fleet hierarchy:
  <fleet>/runtime/          -> routes to top-level body sub-routers
  <body-a>/runtime/         -> routes to body-a's hook + body-a's sub-agents
  <body-b>/runtime/         -> routes to body-b's hook + body-b's sub-agents

Registry entry fields:
  slug          : identifier
  owner_pattern : string that must appear in transcript to claim this session
  hook          : path to hook.sh (leaf node — actually intercepts compaction)
  sub_router    : path to a router.sh at the next level down (branch node)

Dispatch rules:
  - Read transcript once (first 500 lines)
  - Find the most-specific matching entry (longest owner_pattern)
  - If entry has sub_router: call it, pass event via stdin, exit with its code
  - If entry has hook: call hook.sh with AGENT_ROOT env set, exit with its code
  - If no match: exit 0 (Anthropic compacts normally — never break the harness)

Usage:
  echo "$EVENT" | python precompact-router.py [--registry-dir /path/to/dir]
"""

import sys, json, os, subprocess, argparse
from pathlib import Path
from datetime import datetime, timezone

def log(log_path, msg):
    try:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {msg}\n")
    except Exception:
        pass


# ── OS / shell detection layer ────────────────────────────────────────────────
# On Windows there can be MULTIPLE bash environments with INCOMPATIBLE drive mounts:
#   - Git Bash / MSYS2  → mounts drives at /c/   (this is what Claude Code uses)
#   - WSL2 bash         → mounts drives at /mnt/c/
# A bare `bash` on PATH frequently resolves to WSL, which then cannot resolve the
# /c/... or C:/... paths our hooks use, and every hook silently no-ops. We must
# therefore find and invoke the SAME (MSYS) bash that the harness uses.
#
# Override with env HEARTH_BASH=/path/to/bash.exe if detection is ever wrong.

def find_bash() -> str:
    override = os.environ.get("HEARTH_BASH")
    if override and Path(override).exists():
        return override

    if os.name != "nt":
        return "bash"  # POSIX: bare bash is correct

    # Windows: prefer Git Bash (MSYS). Check common install locations.
    candidates = [
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files\Git\usr\bin\bash.exe",
        r"C:\Program Files (x86)\Git\bin\bash.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Git\bin\bash.exe"),
    ]
    for c in candidates:
        if c and Path(c).exists():
            return c

    # Derive from `git` on PATH: <git>/cmd/git.exe → <git>/bin/bash.exe
    try:
        import shutil
        git = shutil.which("git")
        if git:
            root = Path(git).parent.parent  # .../Git
            for sub in ("bin/bash.exe", "usr/bin/bash.exe"):
                cand = root / sub
                if cand.exists():
                    return str(cand)
    except Exception:
        pass

    return "bash"  # last resort (may be WSL — logged by caller)


def to_msys(p: str) -> str:
    """Windows path (C:/foo or C:\\foo) → MSYS form (/c/foo) that Git Bash resolves."""
    if not p or len(p) < 2 or p[1] != ":":
        return p
    return f"/{p[0].lower()}{p[2:].replace(chr(92), '/')}"

def main():
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--registry-dir", default=None)
    args, _ = parser.parse_known_args()

    script_dir = Path(__file__).parent.resolve()
    registry_dir = Path(args.registry_dir).resolve() if args.registry_dir else script_dir
    registry_path = registry_dir / "agent-registry.json"
    log_path = registry_dir / "precompact-router.log"

    # Read event from stdin
    try:
        raw = sys.stdin.buffer.read()
        event_str = raw.decode("utf-8", errors="replace")
        event = json.loads(event_str) if event_str.strip() else {}
    except Exception as e:
        log(log_path, f"ERROR reading event: {e}")
        sys.exit(0)

    transcript_path = event.get("transcript_path", "")

    # Load registry
    try:
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        agents = registry.get("agents", [])
    except Exception as e:
        log(log_path, f"ERROR loading registry {registry_path}: {e}")
        sys.exit(0)

    if not agents:
        log(log_path, "no agents in registry, soft-exit")
        sys.exit(0)

    # Read transcript once
    transcript_head = ""
    if transcript_path and Path(transcript_path).is_file():
        try:
            lines = []
            with open(transcript_path, encoding="utf-8", errors="replace") as f:
                for i, line in enumerate(f):
                    if i >= 500:
                        break
                    lines.append(line)
            transcript_head = "".join(lines)
        except Exception as e:
            log(log_path, f"WARN: could not read transcript: {e}")

    # Find most-specific match (longest owner_pattern present in transcript)
    matched = None
    best_len = 0
    for agent in agents:
        pattern = agent.get("owner_pattern", "")
        if not pattern or not transcript_head:
            continue
        if pattern in transcript_head and len(pattern) > best_len:
            matched = agent
            best_len = len(pattern)

    if matched is None:
        log(log_path, f"no match in {len(agents)} agents for transcript={transcript_path!r}")
        sys.exit(0)

    def resolve(p):
        """Resolve a path that may be relative (to registry_dir) or absolute.
        Returns forward-slash path so bash can accept it on Windows."""
        if not p:
            return ""
        pp = Path(p)
        if pp.is_absolute():
            return str(pp).replace("\\", "/")
        return str((registry_dir / pp).resolve()).replace("\\", "/")

    slug = matched.get("slug", "?")
    sub_router = resolve(matched.get("sub_router", ""))
    hook = resolve(matched.get("hook", ""))
    agent_root = resolve(matched.get("agent_root", ""))

    env = os.environ.copy()
    if agent_root:
        # bash hook needs MSYS form; python sub-routers ignore this env var entirely.
        env["AGENT_ROOT"] = to_msys(agent_root)

    bash_exe = find_bash()

    def dispatch_sh(path: str, label: str) -> "subprocess.CompletedProcess[bytes]":
        """Dispatch a .sh target. Sub-routers are pure-Python (companion .py) so we
        skip bash entirely. Leaf hooks need the CORRECT (MSYS/Git) bash — see find_bash()
        — invoked with cwd+relative-name so the script path needs no drive translation."""
        p = Path(path)
        if p.suffix == ".sh":
            companion = p.with_suffix(".py")
            if companion.is_file():
                sub_dir = str(companion.parent).replace("\\", "/")
                log(log_path, f"   ({label}: calling companion .py directly)")
                return subprocess.run(
                    [sys.executable, str(companion), "--registry-dir", sub_dir],
                    input=raw, env=env,
                )
            log(log_path, f"   ({label}: bash={bash_exe!r} cwd+name)")
            return subprocess.run(
                [bash_exe, p.name], input=raw, env=env, cwd=str(p.parent)
            )
        return subprocess.run([bash_exe, path], input=raw, env=env)

    # Branch: delegate to sub-router
    if sub_router:
        if not Path(sub_router).is_file():
            log(log_path, f"WARN: {slug} sub_router missing at {sub_router!r}, soft-exit")
            sys.exit(0)
        log(log_path, f"-> sub-router: {slug} (pattern={matched['owner_pattern']!r})")
        result = dispatch_sh(sub_router, "sub-router")
        log(log_path, f"   sub-router exit={result.returncode}")
        sys.exit(result.returncode)

    # Leaf: call hook directly
    if hook:
        if not Path(hook).is_file():
            log(log_path, f"WARN: {slug} hook missing at {hook!r}, soft-exit")
            sys.exit(0)
        log(log_path, f"-> hook: {slug} (pattern={matched['owner_pattern']!r})")
        result = dispatch_sh(hook, "hook")
        log(log_path, f"   hook exit={result.returncode}")
        sys.exit(result.returncode)

    log(log_path, f"WARN: {slug} matched but has neither hook nor sub_router, soft-exit")
    sys.exit(0)

if __name__ == "__main__":
    main()
