# WSO2 Agent Manager 1.0.0 — Deployment and Acceptance Checklist

Read [README §4](../README.md#4-deploy-to-on-premises-wso2-agent-manager-100) first. This is a **manual** acceptance checklist; no environment-specific WSO2 deployment is claimed until these checks actually pass.

## Source and infrastructure

- [ ] Changes committed and pushed to a repository reachable by the on-prem build workers
- [ ] `python3 deployment/agent-manager/scripts/prepare-apps.py` run after latest canonical agent changes
- [ ] Six **self-contained** Git project paths in `deployment/agent-manager/apps/aurora-*` committed
- [ ] Synthetic PostgreSQL operational; BANK_INTERNAL_KEY secured, same value on Core, Concierge, specialists and BFF
- [ ] Core `/health` succeeds, C001 seed exists, and Core is reachable **from hosted Concierge and upstream MCP**
- [ ] Upstream Banking MCP `/mcp` runs as a separate service, reachable **from WSO2 gateway egress**
- [ ] MCP DNS-rebinding allowed hosts match private service/ingress Host headers; upstream MCP isn't exposed publicly
- [ ] Banking UI BFF can reach Core via private network, Concierge via authenticated gateway

## Agent Manager MCP governance

- [ ] Organization-level `aurora-banking` MCP Proxy registered for the target environment
- [ ] Seven actual upstream tools discovered; tool URLs target the private **upstream**, managed agents target the **governed** proxy URL
- [ ] Identity-secured OAuth enabled on MCP Proxy (API key alone does not support per-tool identity authorization)
- [ ] Every tool mapped to expected `read/card/dispute/pix` permission; all permission roles assigned to relevant agents
- [ ] No banking tool permission assigned to Concierge; Account & Wellness have read-only permission
- [ ] For each specialist Tool Configuration: injected URL variable **`MCP_SERVER_URL`**, `MCP_AUTH_MODE=agentid`
- [ ] Each specialist has automatically provisioned `AMP_AGENTID_*` values and a successful OAuth token scoped with resource=MCP_SERVER_URL
- [ ] Negative tool access tests from actual gateway identity show denial for prohibited writes

## Platform-hosted Python agents

- [ ] All six agents created as **Custom API Agent** (not the default Chat Agent) with Python 3.12 and `python main.py`
- [ ] All six use the exact individual Git project path, port 8000 and exposed `/openapi.json`
- [ ] Five specialists deployed first; exact deployed Invoke URLs and distinct gateway `X-API-Key` keys gathered
- [ ] Concierge environment has 5 URL/key pairs named `AGENT_*_URL` and `AGENT_*_API_KEY`
- [ ] Concierge environment includes private Core URL and BANK_INTERNAL_KEY
- [ ] Health, chat and protected approve/execute endpoints work through your gateway auth and routing
- [ ] Auto-instrumentation enabled and verified; traces correlated across actual boundaries as supported

## UI and integrated business demo

- [ ] Portal BFF has **only** `CONCIERGE_URL` and `CONCIERGE_API_KEY` for agent access — no specialist keys
- [ ] Browser JavaScript contains no gateway/AgentID secrets
- [ ] BFF forward of chat and approvals works; all specialist calls originate in Concierge
- [ ] Initial Camila balance R$8,450, card charge R$1,280 independent of checking
- [ ] Card block and dispute create two independent approval buttons and persist correct records
- [ ] Pix R$1,250 requires approval, debits checking **once** to R$7,200, and yields a receipt
- [ ] Concierge re-invokes Account and Wellness after Pix commit, and reports projected R$5,100 after R$2,100 obligations
- [ ] English and Spanish responses and the UI are accurate; no raw JSON receipts
- [ ] UI tabs match persisted data after approval; demo reset is restricted to synthetic labs
- [ ] Demonstrate a denied write at gateway and inspect live Agent Manager traces

## Do not claim unless implemented

This sample does not implement production OIDC customer login, FAPI consent/authorization, mTLS agent-to-agent, regulated Pix banking integration, or a persisted pgvector RAG index. It is synthetic banking data for architecture demonstrations only.
