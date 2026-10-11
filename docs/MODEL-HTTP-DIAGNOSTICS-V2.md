# Banco Aurora — governed LLM HTTP diagnostic v2

**Scope:** Discover the cause of `HTTP 503: Governed model unavailable` after Concierge -> Banking Core was repaired with a narrowly scoped NetworkPolicy.

**Important:** TCP/22893 is a known WSO2 API Gateway runtime listener port. A working `nc -z` connection does not prove an accepted authenticated completion. `nslookup` and `nc` can behave differently when short service names, NSS, or host mappings are involved; don't infer DNS failure solely from one BusyBox probe.

This is an **additive diagnostic package**. It does not overwrite `aurora-full-reconcile.py`, rebuild images, redeploy workloads, create NetworkPolicies or rotate credentials.

## Requirements

- Current deployment context `k3d-amp-local`, Concierge pod Ready and managed `LLM_PROVIDER_URL` / `LLM_PROVIDER_KEY` Secret-injected.
- Install the earlier `scripts/diagnose-aurora-llm-path.py` (LLM network diagnostics v1) alongside this script.
- `kubectl` access to Concierge Pod, service and EndpointSlice, and the Secret referenced by Concierge's injected key.
- Kubernetes RBAC to create a temporary **port-forward** if using `--smoke`.

## Install / test

```bash
cd /Users/joaoluiskuntz/wso2mi/Projects/Demo/demo-banking-agent
unzip -o ~/Downloads/banco-aurora-governed-llm-http-diagnostics-v2.zip -d .
python3 -m py_compile scripts/diagnose-aurora-llm-http.py
python3 -m unittest discover -s tests -p 'test_aurora_llm_http_v2.py' -v
```

## Step 1 — read-only matching

```bash
python3 scripts/diagnose-aurora-llm-http.py
```

This finds the Concierge's currently injected LLM URL without printing it, checks that the hostname maps to an actual Kubernetes Service, that the published port is a TCP port on that Service, and that ready EndpointSlice addresses exist. For example, `runtime-gateway.some-namespace:22893` can be a valid internal runtime route, not an external misconfiguration. If it doesn't match a Kubernetes Service or can't be verified, the script **stops and does not guess another target**.

## Step 2 — optional one-call authenticated model check

```bash
python3 scripts/diagnose-aurora-llm-http.py --smoke
```

**This makes one real provider request and may incur a small token charge.** It does not touch synthetic financial records or approvals. It uses the actual Secret-referenced gateway credential, holding it in Python memory only. It launches a temporary loopback-only `kubectl port-forward` to the *verified* runtime Service and issues a small OpenAI Chat Completions request using the same model, `API-Key` header, `Authorization: ''`, and `temperature=0` conventions as the published application. The temporary port-forward is terminated afterward.

Only HTTP status and a fixed classification are displayed. The script never prints the key, request headers, model response body or provider URL; it never passes credentials to subprocess arguments or writes them to disk.

Interpretation:

- `200` + valid `choices`: authentication, routing and upstream model work via the forwarded gateway route. If in-pod `/chat` still returns 503, investigate process environment, model egress / service policies and OpenTelemetry/network transport.
- `401/403`: invalid/unrecognized key, API-Key header mismatch, or gateway access policy.
- `404`: route/base-path mismatch or missing provider deployment.
- `400/422`: unsupported model, request format, or provider policy.
- `429`: rate/usage limit.
- `502/503/504`: gateway/provider upstream connection or service fault; inspect sanitized runtime/provider logs.

**Limit:** The smoke check traverses the Kubernetes Service through a loopback-forward from the local host. It validates the gateway HTTP response, **not the Concierge pod's original egress policy**, and does not assert full demo success. Earlier in-pod `MODEL_TCP=ok` and `GATEWAY_TCP=ok` are still the network-layer evidence.

## After it passes

```bash
python3 scripts/diagnose-aurora-chat.py
```

If Concierge reaches specialists, separately confirm the specialists' published gateway URLs are resolvable and authorized from the Concierge pod. Finally run the existing S01–S06 and gateway-denial authorization checks.
