# Banco Aurora — Concierge minimal container verification v9.3

## Live diagnosis

All six managed deployments reported `active` and Kubernetes `Ready`. The Concierge
Kubernetes Secret mapping/value comparison completed successfully in v9.2, but
its extra process-environment HMAC check could not run because none of the
expected Python executables was available to Kubernetes `exec` in the managed
`main` container.

**This is not evidence of a missing secret.** It is also **not** evidence that
process environment has been directly verified.

## Code behavior

- The existing read-only Kubernetes Service ownership, Ready Pod selection,
  env and envFrom Secret references, and comparison of all 11 expected
  credential values remain mandatory. Errors fail closed.
- When `kubectl exec` successfully returns a Python HMAC proof, it must match
  every expected value. Missing/mismatched proof fails closed.
- If **and only if** all seven Python candidates return Kubernetes OCI
  executable-not-found errors, the process-level proof is marked **NOT proven**
  and the script may continue with the verified Kubernetes Secret wiring.
  It does not claim that PID 1 currently holds the expected secret values.
- Pod-not-Ready, pod missing, permissions, timeouts, arbitrary executable
  failure and mismatched in-process HMAC all remain fatal. No new secrets,
  credentials, or service URLs are logged or changed.
- Overall demo success still requires the Portal gateway connectivity check,
  specialist calls and stateful S01–S06/end-to-end verification. If stale
  process credentials exist, these checks can fail and must be investigated.
- Use `--strict-process` on the standalone checker when process attestation
  must be a non-negotiable security gate, including on minimal images.

No agent resources, builds, cluster configuration, LLM provider, Keys, Helm,
Docker images, Git commits or current runtime deployments are modified.

## Install only over the exact v9.2 baseline

Reconciler SHA256 v9.2:
`55b9645e2869ad12e36cfb74df86173b766dde9ac45028a3936f34719798f398`

```bash
cd /Users/joaoluiskuntz/wso2mi/Projects/Demo/demo-banking-agent
shasum -a 256 scripts/aurora-full-reconcile.py
cp scripts/aurora-full-reconcile.py ~/Downloads/aurora-full-reconcile.before-v9.3.py
unzip -o ~/Downloads/banco-aurora-concierge-minimal-runtime-v9.3.zip -d .
python3 -m py_compile scripts/aurora-full-reconcile.py scripts/verify-aurora-concierge-secrets.py
python3 -m unittest discover -s tests -p 'test_concierge_minimal_image_v93.py' -v
git diff --check
```

## Diagnostics

```bash
# Only Kubernetes Secret references and values. Does not run code in the pod.
python3 scripts/verify-aurora-concierge-secrets.py

# Also attempt a process HMAC proof; prints PARTIALLY VERIFIED if process exec
# is unavailable, while remaining fail-closed for any non-missing-executable error.
python3 scripts/verify-aurora-concierge-secrets.py --runtime

# Stricter policy. Raises error if the process proof is not possible.
python3 scripts/verify-aurora-concierge-secrets.py --strict-process
```

Then, if the checks are satisfactory for this local demo:

```bash
unset AURORA_AGENT_SOURCE_PUBLISH
./scripts/start-everything.sh agents
```

Read the result of the Portal and scenario checks. Do not claim the full demo
works until they succeed. Do not insert an OpenAI key into the UI: the BFF
uses Concierge's gateway API key, not the upstream LLM provider secret.

## Local test scope

The full available test snapshot ran 245 tests, 1 skipped, 0 failures.
The skipped test needs generated OpenAPI apps not included in this fixture.
Python 3.11 grammar is parsed in tests, and `python3 -m py_compile` is required
on the user's actual macOS interpreter. Live cluster behavior cannot be
validated by running these offline tests.
