# Banco Aurora / Agent Manager 1.0.0 — build-lock and MCP configuration race

## Observed incident

`aurora-dispute` was created and gained a valid LLM association, then the
`POST /orgs/default/projects/banco-aurora/agents/aurora-dispute/mcp-configs`
request failed:

```json
{"code":"CONFLICT","message":"A build is already in progress for this agent; try again once it finishes"}
```

The pre-v4 orchestrator configured the MCP connection **before** waiting for the
Agent Manager-managed build. Agent creation can initiate an asynchronous build,
so that order races against the control plane's build lock.

## Fix

1. For *every* specialist, sequence create/reuse -> verify/resume/build source
   -> reconcile LLM -> reconcile MCP -> verify build readiness -> deploy ->
   reuse/provision gateway key. For Concierge, sequence create/reuse ->
   verify/resume/build source -> reconcile LLM -> verify readiness -> deploy.
2. On newly created agents, allow read-only discovery of an implicit
   Agent Manager build before requesting another. Defaults: 30 seconds, one
   inventory read every 3 seconds. If absent throughout the bounded window,
   create one managed build through existing CLI; catch a precise concurrent
   build-lock HTTP 409 and verify the job instead of creating another.
3. A 409 is recoverable only if it is an HTTP 409 with the observed exact
   “A build is already in progress for this agent” message. Read the build
   inventory and verify the commit (including a Git-resolved 8-char commitId),
   repository, branch, and per-agent source path. Resume and await an existing
   matching build; do not build a new one. Once verified, re-run full
   read/compare/write/read-back reconciliation. Never blindly replay POST/PUT.
4. Allow at most 3 configuration/deployment attempts, with a small delay for
   control-plane lock propagation. Other 409 messages, 403/401, wrong commit,
   source mismatch, unknown build, failed build, inventory timeout, and
   unresolved races fail closed with diagnostics.

## Safety boundaries

- No change to start-everything.sh, sync-managed-agent-source.py, Git, Docker,
  cluster/database provisioning, security configuration, or credentials.
- No deletion, recreation, provider reset, key rotation, GitHub Actions, push,
  or commit.
- Existing model-config and MCP verification continue comparing details.
- Unit tests are mocked and **not** proof of six live deployments.

## Installation (repo root)

Verify the existing installed v3 reconciler before overwriting:

```bash
shasum -a 256 scripts/aurora-full-reconcile.py
# expected v3:
# e6dcff0e3ffac1fe8b7dd42aaa877e2ec0e85438ab7078806d1756ec3c0a56bc
```

Unzip the v4 archive from your actual download location into the repository,
after saving a backup. Review `git diff` before proceeding.

```bash
python3 -m py_compile scripts/aurora-full-reconcile.py
python3 -m unittest discover -s tests -p 'test_build_lock_v4.py' -v
python3 -m unittest discover -s tests -p 'test_build_contract_v3.py' -v
python3 -m unittest discover -s tests -p 'test_live_llm_reconciliation.py' -v
git diff --check
```

From the repo root, read-only confirmation of the interrupted specialist:

```bash
amctl agent build list aurora-dispute --project banco-aurora --org default --json
amctl api -X GET /orgs/default/projects/banco-aurora/agents/aurora-dispute/mcp-configs
```

Only after reviewing the build inventory and completing tests, resume:

```bash
unset AURORA_AGENT_SOURCE_PUBLISH
./scripts/start-everything.sh agents
```

`./scripts/start-everything.sh up` remains the full-stack entry point once the
pending runtime checks are satisfied. If the final validation fails, report
those failures; do not mark a partial deployment ready.

## Test coverage

- Correct operation order, all five specialists plus Concierge.
- Existing completed and queued builds, delayed visibility after create,
  no implicit build, build-create 409, wrong revision.
- MCP POST with concurrent creation, MCP POST with a delayed successful write,
  repeated 409 bounded, legitimate write after verified build.
- Exact build-lock 409 only; unrelated conflict, 403, failed build, wrong
  source, unreadable inventory, invisible build all refuse writes.
- Existing v3 LLM-provider and build-contract tests continue to pass in the
  available source snapshot. Application dependency/integration tests outside
  the provided source snapshot still require the project's installed runtime.
