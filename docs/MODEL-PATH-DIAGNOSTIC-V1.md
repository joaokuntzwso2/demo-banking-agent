# Banco Aurora — Governed LLM network diagnostic

Context: after narrowly permitting Concierge -> Core TCP/8100, Concierge can load workflows, but `/chat` now responds `503 Governed model unavailable`. The Concierge source catches **any** managed-LLM exception and intentionally returns 503. This script narrows down **network reachability**, not successful model invocation.

## Install

Unzip into the repo root (adds only files under scripts/, tests/, docs/). Confirm with `python3 -m py_compile scripts/diagnose-aurora-llm-path.py` and `python3 -m unittest discover -s tests -p 'test_aurora_llm_path_v1.py' -v`.

## Run audit first

`python3 scripts/diagnose-aurora-llm-path.py`

Read-only: selects a ready Concierge pod, resolves the platform-injected `LLM_PROVIDER_URL` in memory (including Secret- or ConfigMap-backed env), confirms the presence of `LLM_PROVIDER_KEY` without decoding it, inspects NetworkPolicy selection and verifies the standard internal gateway Service `gateway-default.openchoreo-data-plane:19080`. It does not print actual URL/key values and does not mutate resources.

If the Service is not at that namespace/name, the script stops rather than guessing another one. Determine its real name using `kubectl --context k3d-amp-local get svc -A`.

## Optional live probe

`python3 scripts/diagnose-aurora-llm-path.py --probe`

Creates an **ephemeral BusyBox debug container** in Concierge's *existing* pod. Tests DNS and TCP for the injected model host, then DNS and TCP to `gateway-default.openchoreo-data-plane.svc.cluster.local`. No HTTP request, OpenAI API call, customer data, or secret is transmitted. The ephemeral container is recorded in pod spec until the pod is replaced, even after it terminates.

- `GATEWAY_TCP=failed` with selecting Egress policies: probable network isolation to internal gateway, but not conclusive until policy and endpoints are inspected. Allow only Concierge -> gateway workload on the verified port through a separate scoped NetPol if supported by evidence.
- `GATEWAY_TCP=ok`, `MODEL_TCP=failed`: injected host/port advertised routing mismatch.
- Both TCP `ok`: move to LLM gateway logs, credential/header, provider config and upstream OpenAI status. TCP alone does not confirm correct model routing or auth.

No automatic policy creation, provider mutation, key rotation, rebuild or redeployment.
