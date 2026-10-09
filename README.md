# Banco Aurora — Intelligent Banking with WSO2 Agent Manager

**An end-to-end, fictional banking demonstration: a customer, five specialist AI agents, one orchestration agent, a governed MCP service, a persistent operational banking simulator, and a premium bilingual banking portal.**

> **Status:** runnable local demonstration plus independently buildable Agent Manager 1.0.0 Custom API Agent source bundles and integration manifests. This is **not** a production bank and is **not yet deployed to your Agent Manager installation**. The local Docker Compose profile uses a direct MCP endpoint without the platform gateway; for your actual WSO2 presentation, deploy the six agents separately and use an identity-secured MCP Proxy. Policy retrieval supports optional semantic embeddings and grounded LLM synthesis when the configured OpenAI-compatible provider offers embeddings; otherwise it falls back to deterministic lexical retrieval with document citations. It does not yet persist vectors in pgvector. Automatic instrumentation and hosted governance become active only after you configure and deploy them on the actual Agent Manager environment.

## 1. The business story

**Camila Rocha** is a fictional Banco Aurora premium-banking customer. On checking her account, Camila discovers an unrecognized **BRL 1,280.00 Orion Digital credit-card charge**. She wants to protect her card, open a dispute, pay **BRL 1,250.00** to a verified condominium beneficiary via Pix, and understand whether she can still meet her upcoming bills.

She only needs one conversational interface. The **Aurora Concierge** interprets her request, selects the appropriate specialists and composes a customer-facing response in **English or Spanish**, based on the request. Specialists read shared operational data through MCP, request authorization for potentially sensitive actions, and provide explanations grounded in synthetic policy documents. An explicit customer approval is required before a card is blocked, a dispute is opened, or Pix is executed. The frontend reflects actual persisted state changes, not the language model's claims.

### Demo business values

- **Customer experience:** from complaint to resolution, payment, and cash-flow awareness in one conversation.
- **Efficiency:** independently deployable specialists eliminate hard-coded one-agent-per-screen workflows.
- **Governance:** tool scopes and agent identity are designed to be enforced in WSO2 Agent Manager.
- **Control:** irreversible or consequential actions never execute from LLM output alone.
- **Observability:** persisted business events can be compared with Agent Manager traces after instrumentation.
- **Repeatability:** a one-step database reset restores the initial demonstration.

**Demo calculation:** checking account starts at BRL 8,450.00. A posted BRL 1,280.00 **credit-card charge does not debit checking**. Executing the Pix debits BRL 1,250.00 exactly once, leaving BRL 7,200.00. Remaining planned obligations total BRL 2,100.00. Projection after those obligations: BRL 5,100.00. These projections exclude uncertain future spending, payments and income.

## 2. Architecture and solution boundaries

```mermaid
flowchart TD
 Customer[Camila / Browser] --> Portal[Banco Aurora Portal & BFF :8300]
 Portal --> Concierge[Aurora Concierge / LangGraph :8200]
 Concierge --> Account[Account Intelligence]
 Concierge --> Card[Card Security & Fraud]
 Concierge --> Dispute[Dispute & Resolution]
 Concierge --> Pix[Pix Payments]
 Concierge --> Wellness[Financial Wellness]
 Account --> Proxy[WSO2 MCP Proxy in managed environment]
 Card --> Proxy
 Dispute --> Proxy
 Pix --> Proxy
 Wellness --> Proxy
 Proxy --> MCP[Upstream FastMCP Banking Server]
 MCP --> Core[Banking Core API]
 Core --> DB[(PostgreSQL)]
 Dispute --> Policy[(Dispute Policies)]
 Wellness --> Policy
 Portal --> Core
```

**Dev mode difference:** Compose connects agents **directly to the upstream MCP server** instead of Agent Manager MCP Proxy. The BFF reads state from Core and forwards approval decisions to Concierge. The BFF forwards customer approvals to **Concierge**, which alone decides which specialist `/execute` endpoint to invoke. The specialist executes only its allowed MCP tool, and Concierge re-reads state after commit. The BFF does not call specialists directly.

The technology decisions deliberately follow the style of [`demo-logistics-agent`](https://github.com/joaokuntzwso2/demo-logistics-agent): FastAPI, Python, LangGraph, LangChain/OpenAI-compatible configuration, a separate mock core, separated agents and an integrated portal. Instead of replacing the shared mock core with fake chat responses, this implementation introduces SQLAlchemy/PostgreSQL persistence and an upstream MCP server.

### Agent contracts and deployment units

| Agent | App module / command | Local endpoint | Permission target |
|---|---|---|---|
| Concierge | `deployment/agent-manager/apps/aurora-concierge`; `python main.py` | :8200 | Calls specialist services; owns approval orchestration; no banking MCP permission |
| Account Intelligence | `AGENT_ROLE=account uvicorn agents.specialists:app --host 0.0.0.0 --port 8000` | :8201 | read |
| Card Security & Fraud | `AGENT_ROLE=card ...` | :8202 | read + card block request/execution |
| Dispute & Resolution | `AGENT_ROLE=dispute ...` | :8203 | read + dispute request/execution |
| Pix Payments | `AGENT_ROLE=pix ...` | :8204 | read + Pix request/execution |
| Financial Wellness | `AGENT_ROLE=wellness ...` | :8205 | read |

**RAG:** `agents/retrieval.py` fetches the relevant versioned document by topic and locale, ranks with OpenAI-compatible embeddings when `OPENAI_EMBEDDING_MODEL` is configured (falling back to lexical similarity), then uses the configured chat LLM to synthesize a cited answer from retrieved policy and independently verified banking facts. The tiny bundled corpus is intentionally small; it is not yet a persisted vector database.

**LangGraph implementation:** Concierge executes a `select -> invoke` state graph; the planner optionally uses an OpenAI-compatible LLM to select a **validated list** of specialists; without model credentials, a documented heuristic planner makes the demonstration usable offline. It invokes specialists in parallel and handles failures independently. Specialists are deliberately data-first: they use MCP tools and deterministic policy and business computations, rather than delegating bank amounts, card status or side effects to a generative model.

### MCP tools

| Tool | Read/write | Intended caller |
|---|---|---|
| `get_banking_snapshot` | read | All five specialists |
| `request_card_block` | creates pending approval | Card specialist |
| `execute_approved_card_block` | card update | Card specialist / approved workflow |
| `request_dispute` | creates pending approval | Dispute specialist |
| `execute_approved_dispute` | creates dispute case | Dispute specialist / approved workflow |
| `request_pix` | creates pending approval | Pix specialist |
| `execute_approved_pix` | ledger debit + payment record | Pix specialist / approved workflow |

**No financial operation is real.** The core also checks ownership, approval-action/payload match, idempotency, balances, beneficiary verification and a synthetic maximum Pix amount of BRL 2,500.

## 3. Local prerequisites and quick start

- Docker and Docker Compose v2, available disk for Python image and PostgreSQL.
- Optional: OpenAI-compatible model credentials for LLM-based orchestration; the simulation still runs without a model using fallback intent routing.

```bash
cp .env.example .env
# For a reproducible offline demonstration, OPENAI_API_KEY may remain empty. Optionally set `OPENAI_EMBEDDING_MODEL=text-embedding-3-small` when your provider supports embeddings.
docker compose up --build -d
# On first boot, the core creates tables and seeds customer C001 automatically.
curl http://localhost:8100/health
curl http://localhost:8200/health
open http://localhost:8300
```

Compose services: `db`, `core`, `mcp`, `account`, `card`, `dispute`, `pix`, `wellness`, `concierge`, `portal`. Internal services use the Docker Compose network. Local demo keys and the hard-coded UI session are **insecure example values**; do not expose the stack on the public Internet.

**Reset:** press **Reset demo** in the left navigation, or run `./scripts/seed.sh`. Reset transactionally deletes and reinserts synthetic records without dropping schema tables; use only for synthetic data. `ENABLE_DEMO_RESET=true` is enabled only for the Compose Core service.

### PostgreSQL Core startup readiness and first-run seed (consolidated fix)

Previous copies of this repository performed destructive `DROP TABLE` operations
when installing new demo data, which could deadlock on PostgreSQL locks. Other
copies also seeded the `Account` before flushing its parent `Customer`, causing a
foreign-key failure. **Neither behavior is used for first-run startup now.**

`mock_core/store.py` performs `create_all()` for the synthetic demo schema, then
checks for customer C001. A new database is seeded in one transaction (the
customer row is explicitly flushed before the account row). An existing database
is preserved on restart. The explicit `/admin/reset` operation transactionally
clears and replaces demo rows without dropping tables. PostgreSQL connections
have bounded connection/statement/lock timeouts and boot logs identify the
specific stage where initialization fails.

**If updating from an earlier ZIP:** ensure that you actually unpack the complete
replacement source into the directory where Docker Compose runs. The original
archive had no `scripts/smoke-test.sh` and lacked a Core HTTP health check. The
current `compose.yaml` defines a `core` HTTP health check, and `mcp` has
`depends_on: core: {condition: service_healthy}`.

```bash
ls -l scripts/smoke-test.sh scripts/diagnose-core.sh
docker compose config --quiet
docker compose up -d --build --force-recreate
bash scripts/smoke-test.sh
```

Expected Banking Core response: `{"status":"ok"}`.

For advanced, **read-only** PostgreSQL diagnosis:

```bash
bash scripts/diagnose-core.sh
```

The script prints the database client queries, waits and `pg_blocking_pids()`
that may prevent initialization. It does **not** delete the PostgreSQL volume.
Do not run `docker compose down -v` just to address an application startup error.

### If a specialist returns “Service temporarily unavailable”

An `HTTPStatusError` at the Concierge means the specialist's `/chat` endpoint rejected the call (typically an unhandled upstream exception). Investigate the chain in order:

```bash
docker compose ps
docker compose logs --tail=100 account mcp core
curl -i http://localhost:8100/health
curl -i -H "X-Internal-Key: local-demo-internal-key" http://localhost:8100/state/C001
curl -i http://localhost:8201/health
docker compose exec account python -c "import asyncio; from agents.common import mcp_tool; print(asyncio.run(mcp_tool('get_banking_snapshot', {'customer_id':'C001'})))"
```

The upstream MCP server is reached **inside Docker** at `http://mcp:8101/mcp`. MCP Python SDK host validation can reject the Docker hostname `mcp` with **421 Invalid Host header** unless it is allowlisted. This repository explicitly permits `mcp:*`, along with loopback names, using `MCP_ALLOWED_HOSTS` while keeping DNS-rebinding protection enabled. When deploying on premises, **replace/extend this allowlist with your actual upstream MCP hostnames** and review `MCP_ALLOWED_ORIGINS`; do not disable hostname protection as a blanket workaround. The MCP endpoint speaks MCP protocol, not plain REST; an ordinary browser GET to `/mcp` is not a valid tool invocation.

After fixing configuration, use `docker compose up --build -d` and repeat the curl test at port 8200. You should see an Account Intelligence response with Camila's balance instead of an error.

**Tests:**

```bash
python -m pip install -r requirements.txt
pytest -q
python -m compileall -q agents demo_portal mock_core mcp_server banking_common
```

**Endpoints:** portal `http://localhost:8300`, core docs `http://localhost:8100/docs`, FastMCP upstream `http://localhost:8101/mcp`, concierge `http://localhost:8200/chat`, specialist agents `:8201` through `:8205` at `/chat`.

### MCP SDK compatibility fix (October 2026)

The banking specialists use the **MCP Python SDK v1.25** client transport. The SDK's
`streamable_http_client()` signature accepts `http_client=` but **not**
`headers=` or `timeout=`. Authentication and HTTP timeouts are configured on
an `httpx.AsyncClient` and passed via `http_client=http_client`.
The project pins `mcp==1.25.0` for a reproducible local demonstration and
includes the regression test `tests/test_mcp_compat.py`.

If an earlier copy printed `TypeError: streamable_http_client() got an unexpected
keyword argument 'headers'`, install the corrected code and rebuild:

```bash
unzip -o ~/Downloads/demo-banking-agent-mcp-fix.zip
cd demo-banking-agent
docker compose up -d --build
bash scripts/smoke-test.sh
```

The smoke test runs read-only API checks (including MCP and Concierge), then
verifies the portal is available at `http://localhost:8300`. It does **not**
confirm an actual on-prem Agent Manager deployment: this is the local
Docker Compose topology. Use `docker compose logs --tail=120 core mcp account`
for startup or MCP errors. For a silent curl failure, use
`curl -vS --max-time 10 http://localhost:8100/health` to see whether
it's connection refused, a timeout or another HTTP status; `curl -s` alone
suppresses useful diagnostics.

### Example public-facing chat request

```bash
curl -s http://localhost:8200/chat -H 'Content-Type: application/json' -d '{"customer_id":"C001","message":"Can you check my balance and upcoming bills?","language":"en"}'
```

**Data consistency rule:** dashboard metrics come from `/api/state`, not from assistant strings. Every displayed status can be checked independently against the Core API with an internal key.

## 4. Deploy to on-premises WSO2 Agent Manager 1.0.0

### 4.1 What runs where, and why

**Deploy exactly six agents using Agent Manager's Platform-Hosted → Source Code capability.** Each agent is an independent Python/FastAPI **Custom API Agent** with source committed in this Git repository at `deployment/agent-manager/apps/aurora-<role>`. Select Python 3.12, **Start Command `python main.py`**, **Custom API Agent**, **port 8000**, and `/openapi.json` as the FastAPI OpenAPI document where requested. **Do not select default Chat Agent** for this demo: that standard expects `{message,session_id,context}` → `{response}`, whereas this application uses a richer `/chat` envelope plus `/approve`, `/post-action`, and `/execute`. Do not relabel a Custom API deployment as a Chat Agent simply to make the console Try It view more convenient. Validate the actual Invoke URL and supported path from the deployment page.

**The custom Banking MCP runtime, PostgreSQL, Banking Core, and frontend/BFF are not six additional agent workloads.** Run these as normal Kubernetes or VM services reachable across your on-prem network. In WSO2 Agent Manager, **register the running upstream MCP server as an organization-level MCP Proxy**, then attach the proxy to the five specialists. Agent Manager governs and publishes the proxy; it does not itself replace the upstream MCP server executable. Its built-in *administrative* `/mcp` endpoint is for managing Agent Manager resources, not for accessing customer banking data.

```mermaid
sequenceDiagram
    participant Customer as Browser / UI
    participant BFF as Banking Portal BFF
    participant Concierge as Aurora Concierge (AMP)
    participant Specialist as Specialist Agent (AMP)
    participant Proxy as AMP Banking MCP Proxy
    participant MCP as Upstream Banking MCP
    participant Core as Banking Core / PostgreSQL
    Customer->>BFF: POST /api/chat (English or Spanish)
    BFF->>Concierge: POST /chat + gateway API key
    Concierge->>Specialist: POST /chat + specialist gateway API key
    Specialist->>Proxy: MCP tools/call + AgentID OAuth
    Proxy->>MCP: Forward permitted tool request
    MCP->>Core: Banking API with internal service secret
    Core-->>MCP: Verified synthetic banking data
    MCP-->>Specialist: MCP tool result
    Specialist-->>Concierge: Structured response + pending approval
    Concierge-->>BFF: Aggregated message + next_actions
    BFF-->>Customer: Show customer answer + approval buttons
    Customer->>BFF: Confirm approval
    BFF->>Concierge: POST /approve (gateway + service secret)
    Concierge->>Core: Record verified approval decision
    Concierge->>Specialist: POST /execute (gateway + service secret)
    Specialist->>Proxy: Authorized MCP mutation
    Proxy->>MCP: Execute allowed tool
    MCP->>Core: Commit simulated operation
    Concierge->>Specialist: Read-back via /chat
    Specialist->>Proxy: MCP get_banking_snapshot
    Concierge-->>BFF: Customer receipt + verified updated values
    BFF-->>Customer: Update chat and refresh dashboard
```

**Why this topology matters:** The browser sends chat and approval requests **only to its own BFF**. The BFF talks **only to Concierge** for agentic operations. Concierge is the sole specialist selector and approval orchestrator. It has **no direct MCP banking tool access**; all data and banking mutations are performed by authorized specialists. The BFF separately reads `/state/C001` from Core for the dashboard and may invoke the synthetic admin reset; those are regular UI-support calls, *not agent orchestration*. Protect the BFF and Core with appropriate authentication and network policies beyond this demo.

### 4.2 Detailed agent catalog: responsibilities, inputs, outputs, and communication

Every agent exposes `GET /health` and `POST /chat` on **port 8000** once platform-hosted. Every invocation sends the same structured **internal** request (`message`, `customer_id`, `language`, optional `history`). Specialists return `{agent, message, data, next_actions}`; the Concierge combines them into a single customer-facing envelope. The UI never calls these URLs directly. Language is inferred from message content with a fallback to the UI's English/Spanish preference.

| Deployed agent | Business role and actual code | Receives / calls | Tools, retrieval, results |
|---|---|---|---|
| **aurora-concierge** | `agents/concierge.py`: LangGraph `select → invoke` workflow; intent selection (LLM when configured, heuristics otherwise), fan-out/fan-in, failures, combined bilingual answer; controls approved follow-up | Only customer-facing agent. Receives `/chat` and protected `/approve` from BFF; invokes specialists over HTTPS via `AGENT_*_URL` and `X-API-Key` | **No MCP banking tools**. On approval, validates through Core, calls the authorized specialist `/execute`, then asks Account/Wellness (Pix) or relevant specialist (card/dispute) to re-read state. Cannot invent transaction balances. |
| **aurora-account** | `agents/specialists.py:account`: balance, transactions, card payment vs checking distinction | Concierge `/chat` → identity-governed MCP | `get_banking_snapshot` only. Returns account, transactions, beneficiaries and payment records; does not mutate state. |
| **aurora-card** | `agents/specialists.py:card`: inspect card status, identify suspected fraud intent, prepare block request | Concierge `/chat` and protected `/execute` | Read snapshot → `request_card_block` (creates pending approval only) → `execute_approved_card_block` (only after Core says approved). Reread card status. |
| **aurora-dispute** | `agents/specialists.py:dispute`: check the Orion Digital charge, find relevant dispute policy, explain process, open case after consent | Concierge `/chat` and `/execute`; local `knowledge/policies.json` | Snapshot + `request_dispute` + `execute_approved_dispute`. Policy retrieval is language-aware; optional embeddings and grounded LLM synthesis, with citation such as `POL-DIS-001, v1.0`. No automatic refund. |
| **aurora-pix** | `agents/specialists.py:pix`: parse user-provided BRL amount, match verified condominium beneficiary, check funds/limit, request consent, perform simulated transfer | Concierge `/chat` and `/execute` | Snapshot + `request_pix` + `execute_approved_pix`; idempotency key is `PIX-<approval_id>`. Does **not** pay before approval. After commit, Concierge asks Account+Wellness to recompute balance. |
| **aurora-wellness** | `agents/specialists.py:wellness`: independently calculate obligations and projected remaining cash | Concierge `/chat` → identity-governed MCP + local policy retrieval | Snapshot only. Local curated financial policy + optional semantic embeddings/LLM, with versioned policy citation `POL-FIN-001`. No banking writes and no investment advice. |

**RAG accurately described:** the repository supports retrieval-grounded dispute and financial-wellness answers from bundled versioned policy documents. It supports optional OpenAI-compatible embedding ranking when configured and lexical fallback when offline. This demo **does not** yet implement an external vector index or pgvector storage and must not be presented as if it did.

**Orchestration details:**

1. Concierge receives user intent and language. In `select_specialists`, the LangGraph planner chooses roles from a closed allowlist (using an LLM if credentials are available, otherwise keywords).
2. `invoke_specialists` calls distinct **deployed** endpoints, not in-process Python functions. Each specialist retrieves data using the MCP client and its own AgentID. Results/errors are gathered independently.
3. For `Pix then tell me my balance`, the Concierge deliberately postpones Account/Wellness analysis until **after** the payment approval/commit. A pending payment is never reported as completed.
4. Approval buttons invoke BFF `POST /api/approvals/{id}/decide`; BFF delegates to Concierge `POST /approve`; Concierge records the approval via Core and invokes the appropriate specialist's protected `/execute`, which invokes the governed MCP write tool.
5. Concierge re-queries the required specialists, verifies the persisted outcome and assembles a **human-readable** English or Spanish receipt. If the read-back fails after a successful write, it still reports completed execution and explicitly says that the fresh balance could not be verified.
6. The browser refreshes the UI's Core-backed projection independently. Multiple pending approvals persist so one action does not remove another.

**No direct agent-to-agent MCP:** specialists communicate with the common banking server through the **WSO2 MCP Proxy**. Concierge speaks to specialists using authenticated HTTP API calls (not A2A protocol; adding A2A is a later extension). This demo has an actual LangGraph orchestration step rather than a single model prompting itself to impersonate six agents.

### 4.3 Banking MCP architecture and all seven tools

The upstream server is `mcp_server/app.py`, implemented using Python **FastMCP + Streamable HTTP** on `/mcp` (port 8101). It is *not* a WSO2 agent and is *not* the Agent Manager administrative MCP. The upstream server never directly manipulates SQL; it calls the Banking Core HTTP API with its own `BANK_INTERNAL_KEY`. Core validates customer ownership, immutable approval details, balance, Pix limit, verified beneficiary, single-use approval and idempotency, then commits the PostgreSQL transaction. RAG retrieval occurs **inside Dispute/Wellness agents**, not in this MCP server.

| MCP tool | Allowed agents | Core impact | Requires prior approval? |
|---|---|---|---|
| `get_banking_snapshot` | Account, Card, Dispute, Pix, Wellness | Read account/cards/transactions/obligations/beneficiaries/payments/cases | No |
| `request_card_block` | Card | Creates pending authorization (no card block) | No; requests consent |
| `execute_approved_card_block` | Card | Sets card `blocked` | Yes |
| `request_dispute` | Dispute | Creates pending authorization | No; requests consent |
| `execute_approved_dispute` | Dispute | Opens `under_review` case | Yes |
| `request_pix` | Pix | Creates pending authorization | No; requests consent |
| `execute_approved_pix` | Pix | Writes completed payment and single checking debit | Yes + idempotency |

The actual server performs **seven distinct tool registrations**; the WSO2 proxy *discovers* them rather than deploying a replacement server. The runtime now also enforces a local `TOOLS_BY_ROLE` allowlist so that a mistakenly configured specialist cannot invoke a forbidden tool, but this Python allowlist **does not replace** gateway OAuth tool-level permissions.

#### Required Agent Manager MCP configuration

1. Register one organization-level **MCP Proxy**: name `aurora-banking`, version `v1.0`, context `/aurora/banking`; set its upstream endpoint to the reachable private URL `http://aurora-mcp.aurora-demo.svc.cluster.local:8101/mcp` **only if** the gateway shares DNS/network with that cluster; otherwise use your private TLS ingress. Fetch server info; verify all seven tool names.
2. Select the `dev`/test environment(s) and verify an active egress-capable AI Gateway. Choose **OAuth** for the MCP Proxy **Security** tab (prefer identity-secured access). Keep the upstream service private; do not publish raw MCP access to the Internet.
3. In the Agent Manager authorization configuration, grant tools using the following **sample** scopes (replace the prefix with the actual registered proxy handle displayed in the console): `aurora-banking:read`, `aurora-banking:card`, `aurora-banking:dispute`, `aurora-banking:pix`.
4. Map `read → get_banking_snapshot`; `card → request_card_block, execute_approved_card_block`; `dispute → request_dispute, execute_approved_dispute`; `pix → request_pix, execute_approved_pix`. Create environment roles and grant **read** to all specialists, and the specific write role to only Card, Dispute, or Pix. Do **not** grant any banking MCP role to Concierge. **Map every tool explicitly**; unmanaged/unmapped tools may not be denied as you intend.
5. Open each specialist's **Configure → Tool Configurations → Add MCP Proxy**, choose `aurora-banking`; set the proxy's **injected URL variable name to `MCP_SERVER_URL`**. For OAuth, Agent Manager also automatically injects `AMP_AGENTID_CLIENT_ID`, `AMP_AGENTID_CLIENT_SECRET`, `AMP_AGENTID_TOKEN_ENDPOINT` and `AMP_AGENTID_SCOPES` for each agent/environment (do not put values into your project `.env`). Set **`MCP_AUTH_MODE=agentid`**, `WSO2_MODE=true` on each specialist. The Python MCP client obtains a short-lived client-credentials token with `resource=MCP_SERVER_URL` per RFC 8707, caches it until near expiry, and attaches it to MCP requests.
6. If using API Key MCP security **for a less-secure demo only**, rename the injected variables to `MCP_SERVER_URL` and `MCP_PROXY_API_KEY`, set `MCP_AUTH_MODE=apikey` and `MCP_API_KEY_HEADER=X-API-Key` or the header you configured in the proxy. **API keys do not provide per-tool role restrictions by themselves**, so OAuth is recommended for the governance presentation.
7. Confirm with a **negative authorization test** in the gateway: Account Intelligence must be unable to invoke `execute_approved_pix`, and Pix must be unable to execute a card block. Confirm denied tool responses in gateway/Agent Manager traces. Re-deploy an agent after changing injected proxy connection settings.

References: [Register MCP Proxy](https://wso2.com/agent-platform/docs/v1.0.0/guides/register-mcp-proxy/), [Configure MCP Proxies for an Agent](https://wso2.com/agent-platform/docs/v1.0.0/guides/configure-agent-mcp-proxies/), [Authorize Agent Access to MCP Tools](https://wso2.com/agent-platform/docs/v1.0.0/guides/authorize-agent-access-to-mcp-tools/), [Use AgentID in a Platform-Hosted Agent](https://wso2.com/agent-platform/docs/v1.0.0/guides/use-agentid-in-platform-hosted-agents/).

### 4.4 Prepare the source repository and supporting services

The **ZIP is not enough for Agent Manager to build source**: first commit/push it to a Git repository that your on-prem Agent Manager builder can reach. Since builds use a **Git project path**, each project directory must be independently buildable. `scripts/prepare-apps.py` generates self-contained subfolders with `requirements.txt`, `main.py`, local `agents/`, `banking_common/` and bundled `knowledge/`, so no source imports escape the project path. The checked-in copies are not symlinks.

```bash
# from repository root
python3 deployment/agent-manager/scripts/prepare-apps.py
python3 -m compileall -q deployment/agent-manager/apps
git add .
git commit -m "Prepare Banco Aurora for WSO2 Agent Manager deployment"
git push origin main
```

For standard Kubernetes supporting services, build the shared image using `Dockerfile` at the project root, push it to an accessible internal registry, create an external/synthetic PostgreSQL database and deploy Core, MCP and UI as their own Deployments. Use the provided manifest:

```bash
docker build -t REGISTRY.example.internal/aurora/banco-aurora:1.0.0 .
docker push REGISTRY.example.internal/aurora/banco-aurora:1.0.0
kubectl create namespace aurora-demo --dry-run=client -o yaml | kubectl apply -f -
# Use a secret manager instead of shell history for a real deployment.
kubectl -n aurora-demo create secret generic aurora-demo-secrets \
  --from-literal=DATABASE_URL='postgresql+psycopg://USER:PASS@POSTGRES-HOST:5432/aurora' \
  --from-literal=BANK_INTERNAL_KEY='REPLACE_PRIVATE_DEMO_SECRET' \
  --from-literal=CONCIERGE_URL='REPLACE_AFTER_DEPLOYING_CONCIERGE' \
  --from-literal=CONCIERGE_API_KEY='REPLACE_AFTER_CREATING_CONCIERGE_KEY' \
  --from-literal=DEMO_SESSION_TOKEN='local-demo-session'
# Edit REPLACE_REGISTRY in the manifest to your pushed image URL.
kubectl apply -f deployment/agent-manager/k8s/supporting-services.yaml
kubectl -n aurora-demo rollout status deployment/aurora-core
kubectl -n aurora-demo rollout status deployment/aurora-mcp
```

The Kubernetes manifest is an example for an **isolated synthetic demo**, not a secured production stack. It does not install PostgreSQL, registry image-pull secrets, private ingress, network policies, CSI secrets or your Agent Manager control plane. `aurora-mcp` has only a ClusterIP service; the gateway must actually be able to resolve/reach it. Update `MCP_ALLOWED_HOSTS` to **all exact upstream Host header values** the gateway uses, and configure appropriate gateway-origin rules without disabling DNS-rebinding protections. For a local hybrid topology, you may instead leave Core and MCP in Docker Compose **if** the on-prem gateway can reach a private address for the upstream MCP and all hosted agents can reach Core.

### 4.5 Deploy six Platform-Hosted Custom API Agents from Git

Create an organization/project (example project `banco-aurora`) and attach its deployment pipeline with a development environment. In **Projects → banco-aurora → Add Agent → Platform-Hosted Agent → Source Code**, fill one agent at a time:

| Name in Agent Manager | **Git Project Path** | Start Command | Port / interface |
|---|---|---|---|
| `aurora-account` | `/deployment/agent-manager/apps/aurora-account` | `python main.py` | 8000 / Custom API Agent |
| `aurora-card` | `/deployment/agent-manager/apps/aurora-card` | `python main.py` | 8000 / Custom API Agent |
| `aurora-dispute` | `/deployment/agent-manager/apps/aurora-dispute` | `python main.py` | 8000 / Custom API Agent |
| `aurora-pix` | `/deployment/agent-manager/apps/aurora-pix` | `python main.py` | 8000 / Custom API Agent |
| `aurora-wellness` | `/deployment/agent-manager/apps/aurora-wellness` | `python main.py` | 8000 / Custom API Agent |
| **`aurora-concierge`** | `/deployment/agent-manager/apps/aurora-concierge` | `python main.py` | 8000 / Custom API Agent |

Select your repository URL, branch, Python 3.12, auto-instrumentation when supported, and the source path above. Every app exposes an OpenAPI document at `/openapi.json`; for each specialist, restricted `/execute` remains protected by its own service secret **in addition to** the gateway API key. Deploy **the five specialists first**, then Concierge after their Invoke URLs and API keys are available. Create the appropriate gateway keys under **Security → Credentials** for each specialist. Attach MCP Proxy `aurora-banking` to each specialist, **not Concierge**. Use [Secure Agent Endpoints with API Keys](https://wso2.com/agent-platform/docs/v1.0.0/guides/secure-agent-endpoints-with-api-keys/) and retain **`X-API-Key`** endpoint security enabled.

**Specialist environment (all five, use Secret for sensitive fields)** — see `.env.specialist.example`:

| Variable | Value/source | Required |
|---|---|---|
| `WSO2_MODE` | `true` | Yes; fails closed if no valid MCP authentication mode |
| `MCP_AUTH_MODE` | `agentid` (recommended) | Yes |
| `MCP_SERVER_URL` | **Injected by attached MCP Proxy**; must be its published per-env URL, never Core/upstream URL | Yes |
| `AMP_AGENTID_*` | Four platform-injected identity values; **do not manually set them** | Yes for OAuth |
| `BANK_INTERNAL_KEY` | Secret matching Concierge/Core (used to validate `/execute` requests) | Yes for Card/Dispute/Pix |
| `OPENAI_API_KEY`, `OPENAI_URL`, `OPENAI_MODEL` | Provider-specific variables used only when enabling LLM synthesis (especially Dispute/Wellness) | Optional |
| `OPENAI_EMBEDDING_MODEL` | Embedding model used for ranked policy retrieval | Optional |

**Concierge environment** — copy the key/value names from `.env.concierge.example`, with actual per-specialist **Invoke URL base paths** from Agent Manager:

```dotenv
WSO2_MODE=true
AGENT_ACCOUNT_URL=https://YOUR_AMP_INVOKE_URL_FOR_ACCOUNT
AGENT_ACCOUNT_API_KEY=ACCOUNT_ENDPOINT_API_KEY
AGENT_CARD_URL=https://YOUR_AMP_INVOKE_URL_FOR_CARD
AGENT_CARD_API_KEY=CARD_ENDPOINT_API_KEY
AGENT_DISPUTE_URL=https://YOUR_AMP_INVOKE_URL_FOR_DISPUTE
AGENT_DISPUTE_API_KEY=DISPUTE_ENDPOINT_API_KEY
AGENT_PIX_URL=https://YOUR_AMP_INVOKE_URL_FOR_PIX
AGENT_PIX_API_KEY=PIX_ENDPOINT_API_KEY
AGENT_WELLNESS_URL=https://YOUR_AMP_INVOKE_URL_FOR_WELLNESS
AGENT_WELLNESS_API_KEY=WELLNESS_ENDPOINT_API_KEY
BANK_CORE_URL=http://aurora-core.aurora-demo.svc.cluster.local:8100
BANK_INTERNAL_KEY=REPLACE_WITH_PRIVATE_SECRET
```

**Copy the actual Invoke URL from Agent Manager; do not invent hostnames, ports or path segments.** The code appends `/chat`, `/execute`, or `/approve` to the supplied agent *base URL*. If your console's Invoke URL already includes `/chat`, remove that final segment before assigning an env variable. Make sure the shared `BANK_INTERNAL_KEY` is a secret in Agent Manager, not a public Git value. Each specialist API key is separate. For LLM-based role selection, configure an OpenAI-compatible provider using the runtime's `OPENAI_API_KEY`, optional `OPENAI_URL`, `OPENAI_MODEL`; when a governed LLM provider injects different names, explicitly map them to the runtime variables before assuming it works. Otherwise the deterministic intent router remains active. **This demo has not implemented A2A, JWT end-user delegation or OBO token exchange; service authentication uses gateway API keys plus backend validation.**

### 4.6 Point the UI to the deployed agents — two exact choices

**The UI never needs five agent URLs.** The BFF environment needs exactly **one** managed agent address: the Concierge Invoke URL, plus its matching `X-API-Key` value. No Agent Manager API key is sent to JavaScript. `demo_portal/app.py` forwards `/api/chat` and approval decisions to Concierge and reads dashboard state from Core. The environment file changes belong in the **BFF's server runtime**, not in browser code.

**Option A — Keep the UI/Core locally while agents run in Agent Manager (recommended first test):**

```bash
cd demo-banking-agent
cp deployment/agent-manager/.env.portal.example deployment/agent-manager/.env.portal.local
# Edit the .env.portal.local values, in particular:
# - CONCIERGE_URL (exact deployed base URL, no /chat)
# - CONCIERGE_API_KEY (from Concierge Security > Credentials)
# - BANK_INTERNAL_KEY (SAME secret as on deployed Concierge and local Core)
# The provided hybrid Compose overrides BANK_CORE_URL to http://core:8100
# and uses the local synthetic session token for this private demonstration.
docker compose -f deployment/agent-manager/compose.portal.yaml \
  --env-file deployment/agent-manager/.env.portal.local up --build -d
open http://localhost:8300
```

The hybrid Compose file starts **db, core, upstream mcp, and portal only**, **no local agents**. Agent Manager-hosted specialists and Concierge need network access to Core, and the Agent Manager MCP Proxy needs reachability to the upstream MCP. **Local `localhost` inside a pod is not your Mac.** For a real remote environment you must expose the local Core and MCP using controlled private networking/VPN/ingress or deploy both into the on-prem network first. A portal that can reach Concierge is not enough if Concierge cannot reach Core or specialists cannot reach the MCP Proxy.

**Option B — Deploy the UI/BFF on-premises Kubernetes:** update the `aurora-demo-secrets` values for `CONCIERGE_URL` and `CONCIERGE_API_KEY`, using the actual published Concierge endpoint and its gateway API key; then restart portal:

```bash
kubectl -n aurora-demo rollout restart deployment/aurora-portal
kubectl -n aurora-demo rollout status deployment/aurora-portal
kubectl -n aurora-demo port-forward svc/aurora-portal 8300:8300
# In another shell: open http://localhost:8300
```

To adjust environment configuration without Kubernetes, start `uvicorn demo_portal.app:app` with `BANK_CORE_URL`, `BANK_INTERNAL_KEY`, `CONCIERGE_URL`, `CONCIERGE_API_KEY`, `WSO2_MODE=true`, `DEMO_SESSION_TOKEN=local-demo-session`. **Do not** set `AGENT_CARD_URL`/`AGENT_PIX_URL` etc. on the BFF; they belong to Concierge only.

**Demo security limitation:** this bundled UI uses a fixed synthetic customer (`C001`) and JavaScript `X-Demo-Session: local-demo-session`. Accordingly `DEMO_SESSION_TOKEN` must remain `local-demo-session` in this demo unless you also change its JS client. This is not real customer login or authorization. Keep the UI privately accessible; implement proper OIDC + backend sessions, CSRF protection and end-user-to-account binding before using non-synthetic data.

### 4.7 Verification and diagnostics

1. Confirm each Platform-Hosted agent is `Deployed`; test `GET /health`, `POST /chat`, and OpenAPI `/openapi.json` from its allowed network, using the gateway `X-API-Key` when enabled.
2. Confirm the upstream `aurora-mcp` is reachable **from the Agent Manager gateway**, not just from the laptop. Register the proxy and confirm discovery returns **seven** tools. Ensure the deployed endpoint is not the raw upstream URL.
3. Execute a read-only check against deployed Concierge from an authorized machine (set vars locally; no browser secrets):

```bash
export CONCIERGE_URL='https://COPY_EXACT_INVOKE_URL_BASE'
export CONCIERGE_API_KEY='PASTE_CONCIERGE_KEY'
python3 deployment/agent-manager/scripts/check-remote.py
```

4. Test Card+Dispute approvals and verify separate buttons, case/card state and gateway traces. Reset then do Pix R$1,250, confirm R$7,200 checking balance, R$2,100 obligations and R$5,100 projection.
5. Prove gateway enforcement: from Account's **own identity**, a forbidden `execute_approved_pix` call must receive a denial. The code's local permission guard is an additional defense, not proof of WSO2 gateway enforcement.
6. Inspect the WSO2 Agent Manager Observer/trace UI, validate root Concierge spans and specialist/MCP spans. Cross-process trace continuity depends on gateway and OpenTelemetry configuration; do not claim an end-to-end distributed trace unless the actual trace IDs corroborate it.

**Troubleshooting by status:** `401/403` from agent endpoint → missing/incorrect `X-API-Key` or gateway policy; `401/403` MCP → AgentID/scopes/role grants; `404` → wrong agent base path or Custom API route; `421` upstream MCP → incorrect `MCP_ALLOWED_HOSTS`; `502/503` → Core/MCP/gateway network reachability; missing data but HTTP 200 → inspect upstream snapshot and actual seeded database. When AgentID credentials haven't finished provisioning, redeploy/check injected `AMP_AGENTID_*` variables. A local Compose green status is not evidence of an Agent Manager deployment.

### Official technical references

- [On Your Environment (production-grade on-prem Kubernetes)](https://wso2.com/agent-platform/docs/v1.0.0/guides/on-your-environment/)
- [Create Your First Agent (Git/Platform Hosted)](https://wso2.com/agent-platform/docs/v1.0.0/tutorials/create-your-first-agent/)
- [Register MCP Proxy](https://wso2.com/agent-platform/docs/v1.0.0/guides/register-mcp-proxy/)
- [Configure MCP Proxies for an Agent](https://wso2.com/agent-platform/docs/v1.0.0/guides/configure-agent-mcp-proxies/)
- [Authorize Agent Access to MCP Tools](https://wso2.com/agent-platform/docs/v1.0.0/guides/authorize-agent-access-to-mcp-tools/)
- [Use AgentID in a Platform-Hosted Agent](https://wso2.com/agent-platform/docs/v1.0.0/guides/use-agentid-in-platform-hosted-agents/)
- [Secure Agent Endpoints with API Keys](https://wso2.com/agent-platform/docs/v1.0.0/guides/secure-agent-endpoints-with-api-keys/)

## 5. The exact presenter script (10–12 minutes)

| Segment | Presenter action | Evidence the audience sees |
|---|---|---|
| 0:00 | Open `http://localhost:8300`, explain Banco Aurora and Camila | Premium banking dashboard and initial BRL 8,450 checking balance |
| 1:00 | Show transaction `TXN-ORION` | BRL 1,280 unrecognized card charge; *checking unaffected* |
| 2:00 | Ask: "I don't recognize the R$1,280 Orion Digital charge. Block my card and open a dispute." | Concierge selects Card and Dispute agents; policy version cited; two pending approvals |
| 3:00 | Approve both operations | Card transitions to `blocked`; case transitions to `under_review` |
| 5:00 | Switch UI language to Español; ask "Paga R$1.250 por Pix al condominio" | Spanish answer; Pix specialist selects verified beneficiary; pending approval |
| 6:00 | Approve Pix | Payment receipt ID in conversation, persistent payment record and checking balance BRL 7,200 |
| 7:00 | Ask "¿Me alcanza para los próximos gastos?" | Financial Wellness explains BRL 2,100 obligations, projected BRL 5,100 with document citation |
| 8:00 | Open Agent Activity | Business event IDs and timestamps correspond to persisted changes |
| 9:00 | Open your actual Agent Manager Console | Six deployments, assigned scopes, tool access restrictions, available agent traces |
| 10:00 | Demonstrate read-only agent's denied write-tool request via governed MCP identity | Gateway denial for a tool not granted to that identity |
| 11:00 | Reset the demonstration | Returns to initial snapshot, repeatable for next audience |

**Presenter discipline:** the browser's approval is a *mock consent event* and **not** actual bank-grade step-up authentication or dynamic customer identity federation. Never claim financial regulation compliance or production-grade antifraud accuracy from these samples.

## 6. Evidence and acceptance tests

```bash
pytest -q tests/test_core.py
```

The included tests establish: initial seed correctness; card charge/checking-account separation; dispute case creation with no bank balance impact; missing/invalid approval rejection; approval single-use; Pix debit and payment idempotency; amount mismatch protection; card blocking; internal key rejection; unknown customer rejection.

Additional acceptance tests for a real Agent Manager installation are **manual until that environment exists**: platform-hosted builds, OAuth-scoped tool denial, endpoint authorization, OTel traces, provider routing and proxy network access. These must not be described as complete without execution evidence.

## 7. Directory guide

```text
agents/           concierge LangGraph app + five specialist deployments
banking_common/   application configuration
mock_core/        persistent banking simulator, transactions and audit events
mcp_server/       7 FastMCP tools; upstream service for managed MCP proxy
demo_portal/      branded HTML/CSS/JavaScript UI and FastAPI BFF
knowledge/        English/Spanish versioned policy corpus
scripts/          reset + local health/diagnostics
deployment/agent-manager/ six self-contained Git build folders, upstream MCP bundle, K8s templates, environment examples, remote verifier
tests/            business invariants and approval tests
compose.yaml      local integration profile (direct MCP, no WSO2 runtime)
Dockerfile        shared Python build image
```

## 8. Known limitations and hardening priorities

This is a **functional demo baseline**, not a certified production-ready banking solution. The following require dedicated implementation before any external or production demonstration involving real money, real identity, or real customer information:

1. Replace static sample client session with enterprise OIDC/SSO, CSRF protection, backend session storage, and real multi-factor transaction approval. The sample's approval choice is intentionally only a demo confirmation.
2. Validate the implemented AgentID OAuth client-credentials flow, injected `MCP_SERVER_URL`, gateway tool-role grants and TLS trust against the specific on-prem Agent Manager deployment. Never use direct upstream MCP in deployed mode.
3. For a production knowledge corpus, persist versioned embeddings in pgvector or your enterprise vector search and enforce document-level entitlements. The sample supports live embedding-based similarity retrieval when `OPENAI_EMBEDDING_MODEL` is configured, otherwise it uses local lexical fallback.
4. Expand the conditional grounded LLM synthesis with citation-verification tests, prompt-injection defense, and document version auditing. Without LLM credentials, specialists respond with a deterministic policy extract and verified facts.
5. Implement idempotency with transaction isolation/row-level locking suitable for concurrent Pix requests in PostgreSQL, and bank-grade ledger accounting. Current safeguards cover serial demo calls, not concurrent financial posting.
6. Enable Agent Manager auto-instrumentation and verify trace propagation across HTTP service boundaries; add custom evaluations, on-prem observability dashboards, mutual TLS/JWT service authentication beyond demo API keys, audited admin reset, rate limits, WAF/TLS, secrets rotation and retention.
7. Add versioned schema migrations rather than startup `create_all`; disable the demonstration-only reset entirely in live environments.
8. Replace fixed scenario assumptions (e.g. `TXN-ORION`, `BEN001`, fixed Pix 1250.00) with parameter extraction validated against customer-owned records. The demo prompt suggestions are intentionally tied to one deterministic business story.
9. Ensure all static copy, transaction statuses and numeric display formats are localized; this version localizes primary UI labels and conversational responses but leaves some metadata in English.

**License:** example demonstrator assets. No affiliation with a real bank. All names, account details, amounts, policies and transactions are fictional. No Pix API connection exists.

### Specialist execution endpoint

Each write-capable specialist exposes `/execute` for **server-to-server** execution after **Concierge** has recorded a matching customer approval with Core. Set `BANK_INTERNAL_KEY` on Concierge, the write specialists and Core; configure `AGENT_CARD_URL`, `AGENT_DISPUTE_URL`, `AGENT_PIX_URL` on **Concierge**, NOT on the BFF. In an actual Agent Manager deployment, replace the sample shared internal header with authenticated agent/service identity, policy enforcement and gateway-approved service-to-service access. Browser requests never provide arbitrary tool names to `/execute`.


## Conversational continuation after approved operations (v2)

**Why this matters:** A customer request such as “Pay R$1,250 to my verified
condominium Pix beneficiary, then check my remaining balance” is a *multi-stage
workflow*, not two independent answers. Before the payment is approved, the
Console/Concierge can describe the operation but may not claim that it has
occurred. Once the customer clicks **Authorize**, the Banking Portal BFF calls
the Concierge `/approve`, and **Concierge** invokes the Pix specialist `/execute`. That specialist executes the governed MCP write;
the Core confirms the idempotent payment and commits a debit exactly once.
Concierge then performs the post-action orchestration itself (equivalent to its protected `/post-action` logic).

For successful Pix completion the Concierge calls **Account Intelligence** and
**Financial Wellness** *after* the payment, retrieves their fresh MCP-backed
state, and returns a single localized confirmation including the completed
reference, beneficiary, updated balance, known obligations and projected funds.
This is a read-back **after the commit**, not a speculative subtraction in the
browser. If fresh reads fail, the payment success is still acknowledged but the
balance is explicitly marked unavailable; the result is never raw JSON.
For card blocking and dispute creation, the Concierge likewise asks the
appropriate specialist to verify its new state. `BANK_INTERNAL_KEY` guards the
post-action callback in this local simulation; a production deployment requires
full service-to-service identities and scopes through WSO2 governance.

The portal now keeps pending approvals separately; Concierge owns their execution path. In the combined fraud demo,
approving card blocking **does not erase** the still-pending dispute approval.
`README` and local Compose remain for development; this is not proof of
on-premises Agent Manager deployment or its identity configuration.

A regression test for the post-action formatting and language behavior is in
`tests/test_conversation.py`. Check the chat confirmation and the updated UI
payments, overview and financial insights after authorizing a Pix payment.

### Exact English/Spanish workflow acceptance tests

The corrected end-to-end conversational flow and a browser demo script are
provided in [`docs/CONVERSATION-WORKFLOW.md`](docs/CONVERSATION-WORKFLOW.md).
For the first run use **Reset demo** to restore the account to R$8,450.00.
The package includes pure regression tests in `tests/test_conversation.py`.
