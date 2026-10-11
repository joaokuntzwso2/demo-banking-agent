# Banco Aurora — governed-model Pod-network HTTP diagnostic v3

## Why

The previous diagnostics established:

- Portal login, Banking Core, and Concierge readiness pass.
- A narrowly scoped egress NetworkPolicy repaired Concierge → Banking Core.
- Concierge `/chat` still returns `503 Governed model unavailable`.
- Concierge's injected `LLM_PROVIDER_URL` maps to a ready Kubernetes runtime Service in `default-default`, TCP 22893.
- Local loopback forwarding to the exact Service returned an authenticated OpenAI-compatible `200` response.

A successful **local** port-forward does not prove that the same request works **from Concierge's pod network**. The app also catches and hides any OpenAI SDK error as a generic HTTP 503.

## Installation

Only two new files are installed. They do not overwrite the v9.5 reconciler or earlier diagnostics.

```sh
cd /Users/joaoluiskuntz/wso2mi/Projects/Demo/demo-banking-agent
unzip -o ~/Downloads/banco-aurora-governed-llm-pod-http-v3.zip -d .
python3 -m py_compile scripts/diagnose-aurora-llm-pod-http.py
python3 -m unittest discover -s tests -p 'test_aurora_llm_pod_http_v3.py' -v
```

The earlier `scripts/diagnose-aurora-llm-path.py` (v1) and `scripts/diagnose-aurora-llm-http.py` (v2.1) must be present.

## Preflight — read-only

```sh
python3 scripts/diagnose-aurora-llm-pod-http.py
```

The script matches the injected endpoint to the actual in-cluster ready Service, confirms the API key is backed by a Kubernetes Secret, and reports only which proxy-related variable **names** are configured (not values).

## Single Pod-network LLM completion test

```sh
python3 scripts/diagnose-aurora-llm-pod-http.py --smoke
```

This **changes the Pod's ephemeral-container list** by adding one official `python:3.12-alpine` container. That record remains until the Pod is replaced. It does **not** recreate/restart the main container, change the API key, redeploy managed agents, modify network policies, or access Banking Core accounts. The image must be pullable/allowed by cluster policy; on a denied pull the test stops without changing Concierge's main workload.

Using `kubectl exec -i` stdin, the script supplies the previously injected gateway key to the short-lived diagnostic Python process. The key and full endpoint URL are never included in command arguments, environment variables, persisted scripts/files, or printed output. The diagnostic deliberately copies the key into the ephemeral process memory for this single request; only run it in a trusted local cluster. It sends one potentially billable request with the real Concierge planning system prompt and `hello` as the user message. It reports only HTTP status / transport exception class and whether the completion has choices; it does not print the provider's response body.

It uses Python's standard-library `urllib` with proxies explicitly disabled. It **does not inherit the main container's process proxy settings, OpenAI SDK version, environment variables, or instrumentation**. Thus `HTTP 200` proves **pod network + injected key + model route** work, but not the actual main app's SDK call. A difference between this diagnostic and `/chat` points toward the main container's configuration, SDK/client behavior, proxy settings, or request differences. A non-200 from inside the Pod points to in-cluster routing/policy/provider behavior.

### Interpret

| Outcome | Next step |
| --- | --- |
| `CONCIERGE_POD_MODEL_HTTP=200; completion_valid=true` | Compare app's `AsyncOpenAI` options and runtime proxy settings; inspect underlying exception without logging prompts or secrets. |
| `401 / 403` | Investigate Host/header handling and gateway authorization for pod-origin requests. |
| `400 / 422` | Check model and planning prompt against policy/guardrails. |
| `404` | Check the injected URL path/hostname and virtual-host matching. |
| `429` | Check rate limits and quota. |
| `500 / 502 / 503 / 504` | Examine gateway runtime logs corresponding to request timestamp. |
| `TRANSPORT=gaierror` | Diagnose actual Python DNS resolution; BusyBox nslookup/TCP disagreement is now relevant. |
| Image pull/admission denied | Review cluster policies; do not relax agent security just for this test. |

**Stop after this check and share its sanitized output.** Do not rotate credentials, disable governance, or rebuild the six agents yet.
