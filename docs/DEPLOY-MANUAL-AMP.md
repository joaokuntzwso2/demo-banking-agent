# Manual WSO2 Agent Manager Deployment — Banco Aurora Executive v3

**This repository does not automatically provision WSO2 Agent Manager.** You manually create the six Platform-Hosted Custom API Agents, the MCP Proxy, the LLM provider, the required AgentID roles/policies and optional external Risk Advisor. All browser traffic flows via a FastAPI BFF to **only** the deployed Aurora Concierge; the five specialist URLs are configured **inside Concierge**, not in browser JavaScript.

## 1. What runs where

```text
Customer browser → Banking BFF/Portal → deployed Aurora Concierge (agent gateway URL)
                                         ├→ deployed Account Agent
                                         ├→ deployed Card Agent
                                         ├→ deployed Dispute Agent
                                         ├→ deployed Pix Agent
                                         ├→ deployed Wellness Agent
                                         └→ optional External Risk Advisor
Specialists → OAuth-secured Agent Manager MCP Proxy → upstream Banking MCP → Banking Core/DB
```

For local Docker, no Agent Manager console is used: `bash scripts/run-local.sh`. For on-prem integration the Banking Core/upstream MCP **must be reachable from the on-prem data-plane nodes/gateway**; `127.0.0.1` in your laptop is not a routable Core/MCP endpoint for a remote cluster. Use private DNS, TLS and network rules.

## 2. Git source and supported build entrypoints

After changing any canonical agent file:

```bash
python deployment/agent-manager/scripts/prepare-apps.py
git add agents banking_common knowledge deployment/agent-manager/apps deployment/agent-manager/upstream-mcp deployment/agent-manager/external-risk
```

In the Agent Manager Console, use **Git source**, Python 3.12, start `python main.py`, port **8000**, Custom API Agent. The app paths are:

| Agent | Project app path | Endpoints |
|---|---|---|
| Account | `deployment/agent-manager/apps/aurora-account` | `/health`, `/chat` |
| Card | `deployment/agent-manager/apps/aurora-card` | `/health`, `/chat`, `/execute` |
| Dispute | `deployment/agent-manager/apps/aurora-dispute` | `/health`, `/chat`, `/execute` |
| Pix | `deployment/agent-manager/apps/aurora-pix` | `/health`, `/chat`, `/execute` |
| Wellness | `deployment/agent-manager/apps/aurora-wellness` | `/health`, `/chat` |
| Concierge | `deployment/agent-manager/apps/aurora-concierge` | `/health`, `/chat`, `/approve`, `/workflow/{id}` |

**Deploy specialists first** so you have their actual Invoke **base** URLs and gateway keys before setting Concierge. `AGENT_VERSION=executive-v3` shows up on `/health` and is separate from the platform's immutable build revision.

## 3. Bank Core and custom upstream MCP

Deploy Core and the Banking MCP server as ordinary protected services. `deployment/agent-manager/k8s/supporting-services.yaml` is reference material requiring manual reconciliation; it does **not** deploy Agent Manager's platform agents. Use internal credentials and appropriate HTTPS ingress for upstream MCP.

The upstream exposes `/mcp`; it is **not** Agent Manager's administrative or Observer MCP server. Configure MCP Core read/write credentials, network allowlist and TLS. In Agent Manager register an organization-level MCP Proxy to the upstream HTTPS URL, discover **15** tools and implement every scope in [Platform Scenarios](PLATFORM-SCENARIOS.md), rather than allowing default-permit tools. Attach the proxy to specialists only (not Concierge).

For platform-hosted specialists, set the **Tool Configuration injected URL variable** to `MCP_SERVER_URL`; set `MCP_AUTH_MODE=agentid`. Agent Manager provisions `AMP_AGENTID_CLIENT_ID`, `AMP_AGENTID_CLIENT_SECRET`, `AMP_AGENTID_TOKEN_ENDPOINT` and `AMP_AGENTID_SCOPES`. Do not commit or invent their values. Use per-agent roles. Alternatively use `apikey` mode only where tool-level OAuth authorization is not required; it does not replace role-scoped tool authorization.

## 4. Configure each runtime environment manually

Example files are templates, **not credentials**:

| Location | Copy/use | Purpose |
|---|---|---|
| `deployment/agent-manager/.env.portal.example` | `.env.portal.local` on BFF host | Concierge Invoke URL, API key, Core read, login, session and presenter codes |
| `deployment/agent-manager/.env.concierge.example` | Agent Manager Concierge runtime secrets/config | All five specialist Invoke URLs and API keys; Core approval and role call secrets |
| `deployment/agent-manager/.env.specialist.example` | Agent Manager specialist config | MCP Proxy injected URL, AgentID mode, specialist call/execute credentials |
| `deployment/agent-manager/.env.specialist-apikey.example` | alternative only | API-key MCP mode without per-tool identity controls |
| `deployment/agent-manager/.env.upstream-mcp.example` | upstream service config | Core URL, upstream capability credentials, host validation |
| `deployment/agent-manager/.env.external-risk.example` | external process | Governed proxy URL and a read-only external-agent identity |

**Main portal variables:**

```dotenv
WSO2_MODE=true
CONCIERGE_URL=https://amp-bank.example.internal/managed/aurora-concierge
CONCIERGE_API_KEY=YOUR_REAL_GATEWAY_KEY
CONCIERGE_CALL_KEY=SHARED_PRIVATE_BFF_AGENT_SECRET
CONCIERGE_APPROVAL_KEY=SEPARATE_PRIVATE_APPROVAL_SECRET
BANK_CORE_URL=https://banking-core.example.internal
BANK_CORE_READ_KEY=PRIVATE_CORE_READ_SECRET
BANK_RESET_KEY=PRIVATE_PRESENTER_RESET_CAPABILITY
BANK_CASE_OPERATOR_KEY=PRIVATE_OPERATOR_CAPABILITY
DEMO_ACCESS_CODE=PRIVATE_DEMO_LOGIN_CODE
DEMO_RESET_CODE=PRIVATE_RESET_CODE
DEMO_OPERATOR_CODE=PRIVATE_OPERATOR_CODE
DEMO_SESSION_SIGNING_KEY=LONG_RANDOM_SIGNING_SECRET
DEMO_SECURE_COOKIE=true
```

**Concierge variables:** `AGENT_ACCOUNT_URL`, `AGENT_CARD_URL`, `AGENT_DISPUTE_URL`, `AGENT_PIX_URL`, `AGENT_WELLNESS_URL` and respective `AGENT_*_API_KEY` secrets. The optional `AGENT_RISK_URL` and `AGENT_RISK_API_KEY` enable external risk specialist selection. Configure `CONCIERGE_CALL_KEY`, `CONCIERGE_APPROVAL_KEY`, `SPECIALIST_CALL_KEY`, `SPECIALIST_EXECUTION_KEY`, `BANK_CORE_READ_KEY`, `BANK_APPROVAL_KEY` and `BANK_CORE_URL` server-side. Agent Manager's gateway API key is independent from the application's private `X-Agent-Call-Key` / `X-Internal-Key` headers.

**Managed model:** attach an org-level LLM Service Provider to Concierge in the Console. Set injected names `LLM_PROVIDER_URL` and `LLM_PROVIDER_KEY`; set `USE_LLM_PROVIDER=true`, `REQUIRE_GOVERNED_LLM=true`, and a supported `OPENAI_MODEL`. Do not set the upstream `OPENAI_API_KEY` on managed agent deployments. In deterministic/no-model local development, `USE_LLM_PROVIDER=false` is fine; don't claim this demonstrates managed model guardrails.

## 5. Portal/BFF routing to on-prem agents

To keep Portal, PostgreSQL, Core and MCP locally (or within a reachable test network) while the **six agents are deployed on Agent Manager**, use the provided hybrid file:

```bash
cp deployment/agent-manager/.env.portal.example deployment/agent-manager/.env.portal.local
# Edit with private real values AND replace addresses with reachable network DNS.
python scripts/check-managed-config.py --mode portal \
  --env-file deployment/agent-manager/.env.portal.local

docker compose -f deployment/agent-manager/compose.portal.yaml \
  --env-file deployment/agent-manager/.env.portal.local up --build -d
```

This starts `db`, `core`, `mcp`, `portal` **only**. If WSO2 cannot reach your laptop's Core/upstream MCP, deploy those supporting services in a reachable test environment instead; do not claim the hybrid model is functioning because the portal page loaded.

When the Portal runs behind an HTTPS reverse proxy, use `DEMO_SECURE_COOKIE=true` and set `DEMO_PUBLIC_ORIGIN` if browser-visible origin differs from the BFF request base URL. Secure all service endpoints with proper ingress, DNS and TLS. The code remains a **single synthetic-customer demo**, not bank SSO.

## 6. Configure and validate MCP scope enforcement

Use OAuth in Agent Manager's MCP Proxy Security tab. Assign *every one* of the 15 exposed tools to its documented scope. Create roles for each specialist, then assign roles to their AgentIDs. Test a permitted Account read and a **denied Account Pix execution** at the **actual WSO2 gateway**. The local `TOOLS_BY_ROLE` allowlist is supplemental and insufficient evidence.

## 7. Safe preflight and rollout

```bash
python scripts/check-managed-config.py --mode portal --env-file deployment/agent-manager/.env.portal.local
# Console-injected variables are not stored in Git. The preflight checks local config templates only.
python deployment/agent-manager/scripts/check-remote.py --help
```

Verify all `/health` responses and `agent_version` after deploy; verify actual gateway headers and request path appended by `/chat` and `/approve`. Never put the `X-API-Key` in frontend JS. Run S01–S06 against the accessible Portal only after verifying the synthetic Core and reset endpoint. Perform S07–S08 manually in the Console using [Platform Scenarios](PLATFORM-SCENARIOS.md).

### Authentication and production limits

The portal's access-code login is synthetic and not bank-grade authentication. Approval clicks are not genuine MFA. The Core permits only its synthetic customers and operation payloads. For production systems use customer OIDC, real delegation, regulatory fraud controls, bank payment rails, durable orchestration, replay-protected step-up, risk assessment, KMS, and controlled release procedures; these are outside this demonstration.
