# Banco Aurora / WSO2 Agent Manager 1.0.0 — runtime readiness repair v6

## Confirmed live problem

- All six managed agent Services target TCP 8000 and their Kubernetes startup probes target TCP 8000.
- Their runtime logs showed Uvicorn listening on TCP 8080, causing TCP startup probes to fail, repeatedly restarting main containers and giving deployment status `failed`.
- `inputInterface.port` already declares 8000. Do **not** modify Kubernetes Services, installation, database, gateway, Secret objects, or cluster.
- An OpenTelemetry SQLAlchemy instrumentation warning (`sqlalchemy 2.1.4` versus `<2.1`) is **not** proof of the restart cause; application startup completes after the warning. Investigate separately once TCP readiness works.

## What this archive changes

1. `scripts/aurora-full-reconcile.py`: patch over the **exact v5 SHA-256** `a291159bdb6a0fcfde37837a056f4f61dbaa6f7dbf188ccd59f1f279ddbe92e9`.
   - Include `PORT=8000` when deploying agents. `amctl agent deploy --env` merges with current agent environment configuration (WSO2 v1.0.0); no secrets are sent as plain text.
   - Pin deployments to the verified, existing completed `buildName` rather than implicit latest.
   - Read deployment URLs from `/orgs/default/projects/banco-aurora/agents/{agent}/deployments` (`default.endpoints[]`), *not* agent GET.
   - Never hand a failed or in-progress deployment URL to Concierge. Require `active` and Kubernetes Ready.
   - Verify Kubernetes Service/targetPort/probe and actual pod readiness, wait for readiness after deployment.
   - Reuse healthy, already reconciled deployments via a fingerprint stored in existing private `full-automation-state.json`. No duplicate build or unnecessary redeploy when unchanged and healthy.
2. `scripts/aurora_runtime_contract.py`: pure response validators. Restricts URLs to the actual local AMP gateway origin confirmed in the user logs and rejects unexpected hosts/routes.
3. `scripts/repair-aurora-runtime.py`: standalone default **read-only** inspection and explicit `--apply` repair of existing agents, one by one, without source builds or any Kubernetes writes.
4. `tests/test_runtime_contract_v6.py`: behavioral regression coverage using the observed response format.

## Installation

On the Mac, run from the existing repository root:

```bash
cd /Users/joaoluiskuntz/wso2mi/Projects/Demo/demo-banking-agent

# This must match the installed v5 patch. Stop if it differs.
expected=a291159bdb6a0fcfde37837a056f4f61dbaa6f7dbf188ccd59f1f279ddbe92e9
actual=$(shasum -a 256 scripts/aurora-full-reconcile.py | awk '{print $1}')
[ "$actual" = "$expected" ] || { echo "Unexpected reconciler SHA: $actual" >&2; exit 1; }

cp scripts/aurora-full-reconcile.py ~/Downloads/aurora-full-reconcile.before-v6.py
unzip -o ~/Downloads/banco-aurora-runtime-repair-v6.zip -d .

python3 -m py_compile scripts/aurora-full-reconcile.py scripts/aurora_runtime_contract.py scripts/repair-aurora-runtime.py
python3 -m unittest discover -s tests -p 'test_runtime_contract_v6.py' -v

git diff --check
```

### Read-only audit, before any changes

```bash
python3 scripts/repair-aurora-runtime.py
```

If the port/probe mismatch matches the supplied logs, reconcile five specialist deployments, sequentially, reusing their published builds:

```bash
python3 scripts/repair-aurora-runtime.py --apply
```

If `aurora-account` is already `in-progress`, the script waits for that rollout to settle; it will not submit another deployment in parallel. It refuses to alter a suspended, ambiguous or unexpectedly configured resource.

After all five specialists are **active + Ready**, continue through existing source, AgentID, Concierge and Portal automation:

```bash
unset AURORA_AGENT_SOURCE_PUBLISH
./scripts/start-everything.sh agents
```

The reconciler verifies the same runtime readiness before passing specialist URLs to Concierge. `start-everything.sh agents` continues to run existing strict portal and end-to-end checks, and those may reveal independent blockers requiring diagnosis.

To audit Concierge as well (read-only):

```bash
python3 scripts/repair-aurora-runtime.py --include-concierge
```

Optionally the standalone tool can deploy Concierge with verified specialist URLs and existing credential references:

```bash
python3 scripts/repair-aurora-runtime.py --apply --include-concierge
```

Prefer letting the patched reconciler configure Concierge, as it also handles Portal and subsequent checks.

## What is deliberately NOT done

- No Git commit/push, GitHub Actions, source builds, new provider, new gateway keys, or cluster recreation.
- No direct Kubernetes writes to OpenChoreo-owned resources or Secrets.
- No disabling OAuth/MCP origin authentication/auto-instrumentation.
- No automatic dependency pinning or attempt to conceal instrumentation warnings.
- No claim of end-to-end readiness based on a management-plane deployment request.

If PORT=8000 is overridden after deployment, the script stops and reports the observed PORT/probe mismatch. In that case, inspect the platform-generated runtime environment and workload configuration before any further adjustment.
