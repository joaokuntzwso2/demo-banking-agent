# Banco Aurora — Project Reconciliation v9

## Failure addressed

```
[aurora-agents] MCP Proxy Deployed; HTTPS origin auth, 15 tools and 15 one-tool scopes verified.
ERROR: Command failed (exit 1): amctl project: X Conflict
```

Original `ensure_project()` treated **any** non-zero `amctl project get` as absence and immediately attempted `amctl project create`, allowing an unexpected GET failure or transient backend 409 to lead to a duplicate create.

## Fix

- Reads `amctl project list --org default --json` first (`data.projects[].name`, documented in v1.0.0).
- Reuses exactly matching `banco-aurora` without `get` or `create`.
- If missing from a full list, requires an explicit **not found** from `project get` before one create attempt.
- Never issues project creation on 403, 409, malformed JSON, pagination ambiguity, CLI failures, or mismatched project identity.
- If concurrent create returns 409, accepts it only when a fresh org-scoped read verifies the named project; it does **not** retry POST.
- After successful create, verifies read-back with bounded retries; no second create.
- Does not change MCP, OpenAI providers, agents, builds, env variables, credentials, deployments, cluster resources, or Portal code.

## Version check and installation

Run in your checked-out repo before installing:

```bash
cd /Users/joaoluiskuntz/wso2mi/Projects/Demo/demo-banking-agent
shasum -a 256 scripts/aurora-full-reconcile.py
# Expected v8:
# a12f80b03f3d117a709b15490f7a1a1b318121419fb343aa328ab1ed838cb840
cp scripts/aurora-full-reconcile.py ~/Downloads/aurora-full-reconcile.before-v9.py
unzip -o ~/Downloads/banco-aurora-project-reconcile-v9.zip -d .
python3 -m py_compile scripts/aurora-full-reconcile.py
python3 -m unittest discover -s tests -p 'test_project_reconcile_v9.py' -v
git diff --check
```

Only overwrite the reconciler if the expected v8 checksum matches; if not, inspect and merge changes rather than overwriting later work.

### Read-only live preflight

```bash
amctl context show
amctl project list --org default --json
```

Verify `data.projects[].name` includes `banco-aurora` under `org: default`. Never manually create this existing project.

Then resume without changing your existing six agents:

```bash
unset AURORA_AGENT_SOURCE_PUBLISH
./scripts/start-everything.sh agents
```

The updated project logic should report:

```
[aurora-agents] Project exists: banco-aurora (verified; no create requested)
```

If the list command itself returns a conflict, the reconciler **stops** without writing. The backend read conflict then requires diagnosis; this patch does not mask it. Please supply the outputs of `amctl context show`, `amctl project list --org default --json` (with sensitive details redacted) and the exact error.

This local test suite is not live Agent Manager verification; check subsequent Concierge, Portal and end-to-end gates independently.
