# Banco Aurora — Concierge Banking Core connectivity fix v9.5

Observed: Portal login and Core /api/state return 200, but both Portal and direct
Concierge /chat return 500. Sanitized application tracebacks show httpx.ConnectError
in Concierge. In the published source, Concierge starts /chat by loading workflow
state via the BANK_CORE_URL environment variable. The application fallback is
http://localhost:8100, which points back at Concierge when run in Kubernetes.

The existing reconciler set BANK_CORE_URL only on agent creation, NOT during a
later deployment. This archive makes the same non-secret URL part of every
Concierge managed deployment and provides a safe, targeted repair command.

## Install

1. Verify scripts/aurora-full-reconcile.py SHA-256 is exactly:
   aed5045fbeff46377c76d4a0d2b8aa05963f38cab988a8c3455c2409e4e2310b
2. Back it up, then unzip this archive at the repo root.
3. Compile and test using your local Python 3.11:

   python3 -m py_compile scripts/aurora-full-reconcile.py scripts/repair-aurora-concierge-core.py
   python3 -m unittest discover -s tests -p 'test_concierge_core_connectivity_v95.py' -v
   git diff --check

## Non-mutating audit

   python3 scripts/repair-aurora-concierge-core.py

This confirms Kubernetes Service aurora-core:8100 and its Ready EndpointSlices,
finds the currently Ready managed Concierge main pod, and verifies the effective
BANK_CORE_URL through a literal env value or a Secret/ConfigMap reference.
The actual value is **never printed**. It reports correct/missing/mismatched
or unknown/ambiguous only. No private gateway keys or bank credentials change.

## Apply only for verified missing/mismatched URL

   python3 scripts/repair-aurora-concierge-core.py --apply

This imports the existing reconciler and reuses its verified successful build;
only BANK_CORE_URL and PORT=8000 are sent as plaintext deployment variables
through amctl. Existing managed secrets and specialist endpoints are retained.
The tool awaits Agent Manager active plus Kubernetes Ready. If the Core URL is
already correct, it refuses an unnecessary redeploy. If Kubernetes env source
is ambiguous, the tool refuses an override until it can be verified.

## Recheck the non-transactional chat

   python3 scripts/diagnose-aurora-chat.py
   python3 scripts/diagnose-aurora-upstream.py --since 5m --include-core --include-portal

Once this is resolved, the next possible failure is specialist gateway routing:
Agent Manager publishes http://default-default.am-gateway.localhost:19080/*.
A *.localhost address must NOT simply be used as an in-pod destination.
The Portal BFF already implements an in-cluster gateway URL with an explicit
Host header; the Concierge specialist client may need the same source fix
(which requires reviewed source publication and new managed builds). This
package does NOT silently patch/push agent Git source.

## Separate Console Try Out 401

The published FastAPI /chat additionally requires X-Agent-Call-Key, even after
Agent Manager gateway X-API-Key is validated. A Console Try Out request with
only the test API key can therefore reach Uvicorn but get 401. Do not disable
this application-side shared-secret check or paste gateway credentials into
browser JavaScript. Debug separately after Concierge's Core connection works.
