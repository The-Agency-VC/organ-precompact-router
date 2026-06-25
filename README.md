# organ-precompact-router

Holographic fleet-level PreCompact router for the Hearth agent fleet.

One entry in `~/.claude/settings.json`. Distributed fungal registry: each body
routes for its own territory, no single point of concentration.

## Architecture

```
~/.claude/settings.json
  └── bash <fleet>/runtime/precompact-router.sh
        └── precompact-router.py --registry-dir <fleet>/runtime/
              └── agent-registry.json  [fleet level: top-level bodies only]
                    body-a → body-a/runtime/precompact-router.sh
                    body-b → body-b/runtime/precompact-router.sh
                    ...

body-a/runtime/precompact-router.sh
  └── precompact-router.py --registry-dir body-a/runtime/
        └── agent-registry.json  [body-a level: body-a + its children]
              body-a   → body-a/organs/precompact-intercept/hook.sh  (LEAF)
              child-x  → child-x/runtime/precompact-router.sh

child-x/runtime/precompact-router.sh
  └── precompact-router.py --registry-dir child-x/runtime/
        └── agent-registry.json  [child-x level: child-x + its children]
              child-x  → child-x/organs/precompact-intercept/hook.sh  (LEAF)
```

Same `precompact-router.py` at every level. Each copy reads its local
`agent-registry.json`. Adding a new agent = register in parent's registry only.
Fleet `settings.json` never changes.

## Install

**Fleet level (once per machine):**
```bash
FLEET_ROOT=/path/to/fleet-root bash install.sh --fleet
```

**Body level (for bodies that host children):**
```bash
BODY_ROOT=/path/to/agent bash install.sh --body
```

## Register an agent

After installing the router at the appropriate level, agents self-register via
`organ-precompact-intercept`'s install.sh, which calls `agent-register.py`.

Manual registration:
```bash
python agent-register.py \
  --registry /path/to/parent/runtime/agent-registry.json \
  --slug my-agent \
  --pattern data/agents/my-agent \
  --hook /path/to/my-agent/organs/precompact-intercept/hook.sh \
  --root /path/to/my-agent
```

Or as a branch (body hosting children):
```bash
python agent-register.py \
  --registry /path/to/parent/runtime/agent-registry.json \
  --slug my-agent \
  --pattern data/agents/my-agent \
  --sub-router /path/to/my-agent/runtime/precompact-router.sh
```

## Routing logic

Most-specific `owner_pattern` wins (longest string that appears in transcript head).
Nesting: use full relative path as pattern for children to ensure specificity.

Example (child lives at `body-a/data/monad/child-x`):
- Parent pattern: `data/agents/body-a` (18 chars)
- Child pattern:  `data/agents/body-a/data/monad/child-x` (37 chars) ← wins for child sessions
