# Banco Aurora — Concierge Managed Secrets Verification v8

## Observed failure

The v7 reconciler aborted after verifying all five specialist deployments because
`amctl agent get` does **not** expose the private Secret environment bindings.
Its test `if 'AGENT_ACCOUNT_API_KEY' not in json.dumps(agent_get)` is invalid:
absence from metadata does not prove the variable is absent from the deployed
workload.

## v8 correction

- Retains the original platform-hosted six-agent creation, build, LLM/MCP,
  AgentID, deployment, Portal, and E2E behavior.
- Existing Concierge is **not** deleted, recreated, or assigned new keys.
- Performs Secret verification **after** deploying Concierge and reaching
  both Agent Manager `active` and Kubernetes `Ready`.
- Finds the uniquely owned Concierge Service in the managed data-plane
  namespace, then inspects the `main` container's explicit Secret key refs
  and `envFrom.secretRef` inputs.
- Resolves those references with **read-only** Kubernetes Secret GET calls,
  compares decoded values with `agent-manager-support/secrets.env` only in
  memory, and never prints actual values.
- Challenges the running Python process to produce keyed HMAC digests of
  those same environment variables. This catches old environment snapshots
  even when current Kubernetes Secrets have already been updated.
- Verifies **11 keys**: five `AGENT_*_API_KEY` entries, and six existing
  Banking/Concierge/service authentication keys.
- Rejects missing keys, incorrect values, duplicate or plaintext values,
  malformed Secret refs, ambiguous pods, and unready deployments.
- Makes no Kubernetes changes, no direct Secret writes, and no new provider
  or gateway key requests. No new Git commit or image build is required.

The installed CLI v1.0.0 only supports plaintext `--env KEY=VALUE` values at
`amctl agent deploy`. **Never** use it to pass the five gateway keys.
If a binding is truly absent, use the Agent Manager Console's Deploy >
Configurations and Secrets editor to add it as a **Secret**, then retry.
Do not patch controller-managed Kubernetes Secrets or ExternalSecrets directly.

## Installation

From the repository root:

```bash
shasum -a 256 scripts/aurora-full-reconcile.py
# Must match 8c9534366aef9bdb3ab81e927a2b444a2992740bb583ac24857e23f70258041f
cp scripts/aurora-full-reconcile.py ~/Downloads/aurora-full-reconcile.before-v8.py
unzip -o ~/Downloads/banco-aurora-concierge-secrets-v8.zip -d .
python3 -m py_compile scripts/aurora-full-reconcile.py scripts/verify-aurora-concierge-secrets.py
python3 -m unittest discover -s tests -p 'test_concierge_secret_refs_v8.py' -v
python3 -m unittest discover -s tests -p 'test_build_lock_v4.py' -v
git diff --check
```

## Optional read-only precheck

```bash
python3 scripts/verify-aurora-concierge-secrets.py
```

This uses the existing Running pod if present. If the agent has no Running pod
or is rolling out, the precheck cannot prove its configuration. The normal
reconciler instead waits for active/Ready, then validates it.

## Continue

```bash
unset AURORA_AGENT_SOURCE_PUBLISH
./scripts/start-everything.sh agents
```

After specialist and Concierge readiness, v8 confirms the Secret variables
before launching the Portal. It can then continue into Portal connectivity
checks. This is not a claim that final HTTP scenarios or AgentID negative
OAuth tests have succeeded in the live cluster; the existing `check` action
remains the strict end-to-end gate.

## Security permissions

Read-only verification requires permission to list Services and Pods, read
referenced Kubernetes Secrets, and `kubectl exec` into the existing
`aurora-concierge` main container. The verifier intentionally does not
print Secret values, though API access itself is privileged. Do not run it on
untrusted workstations or pass its Secret contents to logging tools.
