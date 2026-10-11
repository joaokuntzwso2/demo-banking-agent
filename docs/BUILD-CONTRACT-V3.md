# Banco Aurora / Agent Manager 1.0.0 — build identity fix v3

## Verified problem

The live `amctl agent build list aurora-account --project banco-aurora --org default --json` response contains `data.builds[].buildParameters.commitId`, value `63479426`. The existing v2 implementation looked only for 40-hex `commitSha`-type fields and could fail to reuse an otherwise successfully completed build. The latest `aurora-card` run stopped for this reason (its individual build inventory has not yet been provided).

## Changes

- Resolve version 1.0.0's `buildParameters.commitId` (8–40 hex) along with existing 40-character commit fields.
- Verify source metadata (`repoUrl`, `branch`, `appPath`) against the intended agent, and for abbreviated commit IDs ask local Git to resolve `prefix^{commit}` uniquely to the published full SHA. A string prefix comparison alone is not accepted.
- Reuse a completed matching build without a `build create`, including when the private build-state file has no record. Resume matching queued/running builds and pin `buildId` when reported.
- Reject ambiguous or conflicting commits, unexpected source coordinates, and unrecognized build states.
- Never treat a changing build identity by itself as evidence that a new build contains the requested source SHA.
- Preserve existing LLM `envMappings` parsing, MCP, gateway credentials, AgentID, cluster, and start-everything orchestration unchanged.
- Replace obsolete legacy LLM unit tests with fixtures derived from actual Agent Manager 1.0.0 account/card JSON. Update build progress tests to simulate the required pre-create read-only inventory. Unsafe unbound-model automatic PUT expectations are *not* restored.

## Install safely

Requires your current reconciler to match SHA-256:

`2b2e6c5f68a00a60dd3e5e7089c17082ed4d9ba3dccea864631c8ee0a9ad05b4`

Do **not** apply if the hash differs without reviewing the diff.

```bash
cd /Users/joaoluiskuntz/wso2mi/Projects/Demo/demo-banking-agent
shasum -a 256 scripts/aurora-full-reconcile.py
cp scripts/aurora-full-reconcile.py ~/Downloads/aurora-full-reconcile.before-v3.py
unzip -o ~/Downloads/banco-aurora-amp100-build-contract-v3.zip -d .
python3 -m py_compile scripts/aurora-full-reconcile.py
python3 -m unittest discover -s tests -p 'test_build_contract_v3.py' -v
python3 -m unittest discover -s tests -p 'test_build_progress_v10.py' -v
python3 -m unittest discover -s tests -p 'test_llm_binding_v11.py' -v
python3 -m unittest discover -s tests -p 'test_llm_environment_v12.py' -v
python3 scripts/diagnose-aurora-llm.py --agents aurora-account aurora-card

git diff --check
git status --short
```

## Before resume: read-only checks

```bash
amctl agent build list aurora-card --project banco-aurora --org default --json
amctl agent build list aurora-account --project banco-aurora --org default --json
git rev-parse HEAD
git rev-parse --verify --quiet '63479426^{commit}'
```

Inspect `aurora-card`'s current build record. It must have a matching SHA abbreviation, source repo, branch and agent path; otherwise this patch safely refuses to reuse it, and further diagnosis is required. In particular, if a build returns a different commit, it may legitimately require a new build; the script will not treat it as healthy.

When read-only results match:

```bash
unset AURORA_AGENT_SOURCE_PUBLISH
./scripts/start-everything.sh agents
```

Then only after the full `agents` action and its end-to-end `check` action pass:

```bash
./scripts/start-everything.sh up
```

## Test status and exclusions

In the available extracted source snapshot, `python3 -m unittest discover -s tests -p 'test_*.py' -q` completed **123 tests, 1 skipped**. This snapshot does not include the full Mac repository: notably `test_core.py` and `test_executive_scenarios.py` need SQLAlchemy and may still error in an unprepared Python environment. The `git rev-parse` calls are mocked in unit tests; the actual Git object is verified by the patched script on the user's Mac. Managed agent deployment and six-agent E2E gates are not claimed passed without live evidence.
