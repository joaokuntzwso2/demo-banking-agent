# Banco Aurora Portal / customer-facing Concierge — guarded integration v7

This is an overlay on **v6 runtime repair**, not a reinstall. It does not edit Kubernetes Services or Secrets, recreate agents, rebuild managed agent images, access the organization's OpenAI provider key, or commit/push Git changes.

## Architecture and credentials

Browser `demo_portal/static/app.js` → same-origin `/api/chat` → `demo_portal/app.py` (private BFF) → Agent Manager `aurora-concierge` invoke URL with `X-API-Key` → five specialist agents and governed OpenAI LLM provider.

**Never put the upstream OpenAI API key in `.env` for the browser, a `VITE_*`/`NEXT_PUBLIC_*` variable, or a public JS bundle.** Agent Manager already has organization provider `aurora-openai`; its LLM bindings inject `LLM_PROVIDER_URL` and `LLM_PROVIDER_KEY` into managed agents. The Portal requires `CONCIERGE_URL` and `CONCIERGE_API_KEY`, *server-side only*. Those are different credentials.

## Why additional routing is needed in k3d

Agent Manager publishes `http://default-default.am-gateway.localhost:19080/aurora-concierge` as an external invoke URL. `*.localhost` is not an appropriate upstream address for a pod. In this **known** local cluster the data-plane Service exists at `gateway-default.openchoreo-data-plane.svc.cluster.local:19080`.

The patch detects the exact approved localhost URL *only while running inside Kubernetes* and sends HTTP to the internal Service with the original Host header for virtual-host routing. It preserves the existing `X-API-Key`, origin/auth policy, and public-console configuration. Any unrecognized localhost endpoint is rejected.

## Install (once, from project root)

1. Back up `scripts/aurora-full-reconcile.py` and `demo_portal/app.py` before applying.
2. Confirm the current v6 reconciler SHA-256 equals:
   `d2a78dc8e0f65e683c04069435d9d1a47284759d187246e13d7e242fb2d351e4`.
3. Unzip `banco-aurora-portal-integration-v7.zip` into the repo.
4. Run `python3 scripts/patch-aurora-portal-gateway.py --apply`. This refuses unexpected BFF source structure and is idempotent.
5. Run focused tests `python3 -m unittest discover -s tests -p test_portal_gateway_v7.py -v`.
6. Run `unset AURORA_AGENT_SOURCE_PUBLISH; ./scripts/start-everything.sh agents` to reconcile the already existing managed agents, configure the Portal's private URL and gateway key using the current `agent-manager-support/start.sh support` flow, rebuild/redeploy the *support Portal image* if needed, and verify actual in-cluster gateway reachability. It will then attempt the existing stateful S01–S06 and AgentID-negative test suite.

The published six managed source applications are not changed. The support image may be rebuilt because `demo_portal/app.py` changed. If you want this to survive a clean Git checkout, review and **manually commit/push** these source changes. No GitHub Actions are configured by this package.

## Standalone read-only check

After Concierge and Portal are running:

`python3 scripts/verify-aurora-portal.py`

This compares the URL with Agent Manager's **active** deployment, confirms the actual running Portal process has the same URL/key as the local private secrets file by nonce-HMAC **without revealing the key**, ensures no upstream OpenAI provider key is injected into Portal, and requires an authenticated `GET /openapi.json` to Concierge through the internal data-plane gateway to return 200. A `Ready` Portal without gateway connectivity is **not** a pass.

A successful OpenAPI check proves reachability and gateway authentication, not a complete chat journey. The existing `./scripts/start-everything.sh check` runs actual authenticated Portal-to-Concierge S01–S06 scenarios. It also requires the separate configured `AURORA_MCP_AUTHZ_VERIFY_CMD`; do not disable or replace that test with `true`.

## Full fresh `up`: conditions

`./scripts/start-everything.sh up` attempts Colima/k3d + Agent Manager via the repo starter, banking backend, tunnel, MCP proxy, six managed agents, Portal, and strict checks. It is not fully unattended if any prerequisites are missing: local Colima/k3d/kubectl/amctl/cloudflared, interactive Agent Manager OAuth login, the first-time OpenAI provider key (entered securely into the provider, not the UI), a Git remote branch containing the managed source commit, Cloudflare origin authentication, and a **real** gateway-level negative OAuth test configuration.

Do not treat the `up` entrypoint as a guaranteed one-command first-install from a brand-new machine until the actual support scripts, security provisioning and full clean-environment run have been verified live.

## On failure

- `Portal BFF differs`: do not bypass the strict patcher; inspect current `demo_portal/app.py`.
- `Portal missing private Concierge URL/key`: reconcile the six managed agents first; do **not** rotate the LLM provider key.
- `Portal env mismatch`: inspect `agent-manager-support/start.sh` and `k8s/portal.yaml` secret injection; do not inject keys into JavaScript.
- `Authenticated gateway HTTP != 200`: inspect data-plane service DNS, HTTPRoute/Host matching and the actual Concierge gateway API key; do not disable OAuth/API key security.
- `S01–S06` failure: use the scenario report for the route or banking logic failure.

Security note: the existing `agent-manager-support/start.sh` and portal Kubernetes manifest were not supplied here; this patch reuses their existing deployment contract and checks the result. Their internals have not been independently audited in this package.
