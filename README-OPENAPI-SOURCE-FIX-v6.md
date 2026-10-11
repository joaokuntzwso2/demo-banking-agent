# Banco Aurora — Agent OpenAPI Source Fix v6

## What this fixes

Stage 5 currently stops with:

```
ERROR: aurora-account: published build folder must contain main.py and openapi.json
```

The source reconciler (`scripts/aurora-full-reconcile.py`, v5) requires the generated `openapi.json` file for all six `custom-api` agents, but `prepare-apps.py` (v4) did not generate that file. The previous source-sync workflow therefore deleted any pre-existing `openapi.json` in the regenerated app folders, while reporting the generated source as synchronized.

This patch changes the canonical generator to emit six deterministic OpenAPI 3.0.3 documents, matching the real FastAPI routes and principal request payload schemas. It strengthens the source-sync preflight to validate all six specs **before any Git mutations** and includes the generator script itself when explicitly committing source files. It does not change GitHub Actions configuration and does not automatically push files merely by being installed.

## Apply

From `demo-banking-agent` repository root:

```bash
unzip -o ~/Downloads/Banco-Aurora-Agent-OpenAPI-Fix-v6.zip
python3 -m py_compile deployment/agent-manager/scripts/prepare-apps.py scripts/sync-managed-agent-source.py
python3 -m unittest discover -s tests -p 'test_generated_openapi.py' -v
python3 -m unittest discover -s tests -p 'test_source_sync.py' -v
```

Preview differences without modifying files or pushing:

```bash
python3 scripts/sync-managed-agent-source.py check
```

This **should report a source difference** until the new OpenAPI files are generated and published. This is expected and is not a new Agent Manager failure.

### Keep GitHub source-only / no Actions

First disable GitHub Actions in the repository Settings → Actions → General → Actions permissions, selecting **Disable actions**, and save. If you use GitHub CLI, check that the setting is false:

```bash
gh api repos/joaokuntzwso2/demo-banking-agent/actions/permissions --jq '.enabled'
```

It must print `false` before you push if your goal is to avoid GitHub Actions. An already-running workflow should be cancelled separately.

Then **explicitly authorize** the source-only sync and push:

```bash
AURORA_AGENT_SOURCE_PUBLISH=1 python3 scripts/sync-managed-agent-source.py ensure
```

This checks source safety, stages the approved agent build input paths (including the generator), commits, pushes to `main` and verifies its SHA. It does not invoke GitHub Actions itself, but GitHub may trigger configured workflows on pushes if you have not disabled them.

Finally resume **agent deployment only**:

```bash
unset AURORA_AGENT_SOURCE_PUBLISH
./scripts/start-everything.sh agents
```

No need to restart Agent Manager, PostgreSQL, MCP, Cloudflare tunnel or the platform cluster.

## Files included

- `deployment/agent-manager/scripts/prepare-apps.py` — deterministic OpenAPI generator.
- `scripts/sync-managed-agent-source.py` — fail-closed validation of all six generated specifications, and include generator in source publishing.
- `tests/test_generated_openapi.py` — schema shape, path, repeatability and source-route checks.
- `tests/test_source_sync.py` — safe sync tests including rejection of missing OpenAPI before a push.

## Limitations

This patch corrects source-generation and Git-build inputs, not Agent Manager's live tool-binding and gateway-key API compatibility. Those later deployment stages still need to pass on your WSO2 1.0.0 installation. No credentials are included in the ZIP.
