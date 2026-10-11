# Banco Aurora — Concierge in-pod secret verification hotfix v9.2

## Root cause (observed live)

All six managed deployments reached `active` / Kubernetes `Ready` on port 8000.
The v9.1 verification then attempted `kubectl exec ... -c main -- python -c ...`,
but the managed Python 3.12 image did not provide a `python` executable on `$PATH`.
That error concerns the verifier executable name, **not missing gateway credentials**.

## Changes

- `scripts/aurora-full-reconcile.py`: read-only in-pod HMAC-based environment proof tries
  `python3` first, then a small fixed list of Python 3/known runtime paths.
  Fallback occurs **only** for an OCI `exec` executable-not-found failure.
  Pod-not-found, permission denied, process failure and timeouts stop verification.
- `scripts/verify-aurora-concierge-secrets.py --runtime`: new read-only command to
  validate Secret references and runtime injection before running the full deployment.
- `tests/test_concierge_exec_v92.py`: ten focused safety/portability tests.

No API keys or expected credential values are included in the Kubernetes exec argv,
process stdout, scripts or tests. The verification still fails closed on any
missing or mismatched key. Does not redeploy agents, modify Secrets, reset resources,
rotate credentials, rebuild images, commit/push source or bypass checks.

## Install (repository root)

Confirm the v9.1 reconciler SHA-256 first:

`61266dab1b8e3c3c25df455ce65108bc585b4c792cd084860ef4fa62d9be7467`

```bash
cd /Users/joaoluiskuntz/wso2mi/Projects/Demo/demo-banking-agent
shasum -a 256 scripts/aurora-full-reconcile.py
cp scripts/aurora-full-reconcile.py ~/Downloads/aurora-full-reconcile.before-v9.2.py
unzip -o ~/Downloads/banco-aurora-concierge-exec-v9.2.zip -d .
python3 -m py_compile scripts/aurora-full-reconcile.py scripts/verify-aurora-concierge-secrets.py
python3 -m unittest discover -s tests -p 'test_concierge_exec_v92.py' -v
git diff --check
```

Expected v9.2 SHA-256:

`55b9645e2869ad12e36cfb74df86173b766dde9ac45028a3936f34719798f398`

## Verify and resume

Read-only, verify live Kubernetes Secret references and pod env:

```bash
python3 scripts/verify-aurora-concierge-secrets.py --runtime
```

If verified, run:

```bash
unset AURORA_AGENT_SOURCE_PUBLISH
./scripts/start-everything.sh agents
```

This should reuse the completed six builds and healthy agent deployments.
It still needs to pass Portal wiring, live connectivity, gateway auth and E2E
banking scenarios before calling the entire demonstration successful.

## Safety / troubleshooting

- Do **not** paste OpenAI keys into the UI; the Portal uses Concierge's gateway key.
- A `python3` executable is expected in the managed Python image but cannot be
  guaranteed until the live command succeeds.
- If no interpreter exists, the script stops with an explicit error; do not disable
  secret verification or manually alter the managed Pod.
- The complete local available source snapshot passed 238 tests (1 skipped) with
  Python 3.13. Python 3.11 grammar was also parsed explicitly; Mac Python 3.11
  must be validated by the user commands above.
