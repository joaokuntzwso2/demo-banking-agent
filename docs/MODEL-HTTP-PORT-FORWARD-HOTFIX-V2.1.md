# Banco Aurora — LLM HTTP port-forward diagnostic v2.1

## Scope

Fixes the v2 **diagnostic-only** false negative `NOT VERIFIED: Gateway Service port-forward could not be established`. Kubectl normally prints `Forwarding from 127.0.0.1:...` to **stdout**, while v2 discarded stdout and watched only stderr.

The v2.1 script captures **both** streams in memory, reads the automatic localhost port, and issues the same one-request governed model check as v2. Errors are classified by fixed messages; raw kubectl output is never printed. `kubectl port-forward` is terminated by the diagnostic. No project, agent, credential, policy, build, or gateway setting is mutated.

## Install

```bash
cd /Users/joaoluiskuntz/wso2mi/Projects/Demo/demo-banking-agent
shasum -a 256 scripts/diagnose-aurora-llm-http.py
# Expected v2: 393bc7c96d3415cdf6978d864dfe79ad9c24fa11d2680c5ae70a8be7df25e799
cp scripts/diagnose-aurora-llm-http.py ~/Downloads/diagnose-aurora-llm-http.before-v2.1.py
unzip -o ~/Downloads/banco-aurora-governed-llm-http-forward-v2.1.zip -d .
python3 -m py_compile scripts/diagnose-aurora-llm-http.py
python3 -m unittest discover -s tests -p 'test_aurora_llm_forward_v21.py' -v
python3 -m unittest discover -s tests -p 'test_aurora_llm_http_v2.py' -v
```

## Execute

```bash
python3 scripts/diagnose-aurora-llm-http.py --smoke
```

This can send **one small billable OpenAI-compatible completion request**, and reads the existing Secret-backed managed gateway credential in memory only. It prints a sanitized HTTP status and classification, never the response body or credential.

### Interpretation

* `HTTP 200` plus valid `choices`: provider HTTP succeeds via the forwarded gateway. Recheck Portal/Concierge and, if it still fails, investigate in-pod runtime, egress and proxy settings.
* `HTTP 401/403`: injected gateway API-key authentication or authorization.
* `HTTP 404`: provider route/base path.
* `HTTP 400/422`: model name, payload or policy.
* `HTTP 429`: quota/rate limit.
* `HTTP 502/503/504`: upstream gateway/provider connectivity or configuration.
* `NOT VERIFIED: Gateway Service port-forward ...`: the corrected diagnostic provides a sanitized category (RBAC, service target, listener, other). No model request was made.

**Limits:** The model test uses a host-origin port-forward, not an HTTP request from Concierge's own process. HTTP success does not demonstrate in-pod connectivity. The active narrow Concierge -> Core TCP/8100 egress NetworkPolicy should remain in place.
