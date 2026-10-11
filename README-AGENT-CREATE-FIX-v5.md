# Banco Aurora — Agent Manager agent-creation fix (v5)

## Problem

Stage 5 created the project `banco-aurora` and stopped while creating the first platform-hosted Custom API agent:

    ERROR: Command failed (exit 2): amctl agent: See Agent Manager logs (sensitive create arguments withheld).

The original CLI command omitted `--openapi-spec` even though all six generated Git bundles contain `openapi.json`. The old error handler also hid CLI flag-validation output, so the exact original reason cannot be conclusively established from the log.

## Changes

- Adds `--openapi-spec openapi.json` to the Custom API create request.
- Verifies that each generated bundle has `main.py` and a valid OpenAPI 3.x `openapi.json` with paths before calling Agent Manager.
- Checks the installed `amctl agent create --help` for required CLI options before passing any secrets.
- Redacts values passed via `--env-secret` and surfaces relevant CLI error lines if the server or CLI refuses the request again.
- Does not create GitHub workflows, commit changes, push changes, recreate the project, or touch Kubernetes/MCP/tunnel deployments.

## Apply to existing repo

    cd /Users/joaoluiskuntz/wso2mi/Projects/Demo/demo-banking-agent
    unzip -o ~/Downloads/Banco-Aurora-Agent-Create-Fix-v5.zip
    python3 -m py_compile scripts/aurora-full-reconcile.py
    python3 -m unittest discover -s tests -p test_reconciler.py -v
    amctl agent create --help | grep -E -- '--openapi-spec|--repo-path|--env-secret'
    ./scripts/start-everything.sh agents

Use `agents` rather than `up` to restart from Stage 5 without rerunning infrastructure stages. `agents` performs its read-only Git publication preflight; it does not push unless `AURORA_AGENT_SOURCE_PUBLISH=1` is explicitly set. Do not set that variable for this retry.

## Security and verification

- No Github Actions files are included. Disable repository Actions permissions separately if you don't want any push to run CI workflows.
- This patch has passed local unit tests but **was not executed on your Mac or Agent Manager cluster**. If Stage 5 still fails, the new error includes redacted CLI diagnostic text; capture it for follow-up.
- Agent Manager creation/deployment may still encounter other release-specific API behavior not exercised by this patch.
