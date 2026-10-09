# Manual On-Premises WSO2 Agent Manager Acceptance Checklist

The platform deployment remains **manual**. This checklist verifies the product integration after deploying the application components. It does not automate or attest to deployment in an installation we cannot access.

Start with [README — Manually deploy on WSO2 Agent Manager](../README.md#6--manually-deploy-on-wso2-agent-manager-on-premises) and [MCP governance matrix](GOVERNANCE-MATRIX.md).

## Repository and network

- [ ] Canonical agent sources and regenerated `deployment/agent-manager/apps/aurora-*` source projects are committed.
- [ ] PostgreSQL + Core are private. Core has its own read/write/approval/reset capability keys (`BANK_CORE_READ_KEY`, `BANK_CORE_WRITE_KEY`, `BANK_APPROVAL_KEY`, `BANK_RESET_KEY`); no broad shared all-purpose key.
- [ ] Upstream Banking MCP runs privately and has **only** the Core read and write credentials it needs; MCP Proxy reaches its `/mcp` URL.
- [ ] `MCP_ALLOWED_HOSTS` includes the actual gateway-to-upstream Host value without disabling DNS rebinding protection.
- [ ] A TLS-secured BFF reaches the managed Concierge URL, and can reach the private Core dashboard read API. No private service is exposed to the public Internet.

## Agent Manager MCP governance — manually configured

- [ ] An MCP Proxy targets the upstream MCP, not an agent deployment.
- [ ] All 15 tools are discovered and individually mapped to scopes. **Check uncovered tools**: the documented default may allow them to authenticated callers.
- [ ] The five specialists receive their governed `MCP_SERVER_URL`; `MCP_AUTH_MODE=agentid` is configured and AgentID credentials are provisioned by the platform.
- [ ] Role-scoped permissions allow reads to Account/Wellness, appropriate card tools to Card, dispute tools to Dispute, Pix tools to Pix; Concierge has no direct banking MCP tools.
- [ ] A deliberate Account→execute-Pix test is **denied at the MCP Proxy**, not just by Python code.

## Six agents — manually deployed

- [ ] Each `deployment/agent-manager/apps/aurora-{role}` directory builds independently as a Python 3.12 Custom API Agent, `python main.py`, port 8000.
- [ ] The five specialists have `SPECIALIST_CALL_KEY`; Card/Dispute/Pix also have `SPECIALIST_EXECUTION_KEY`. Distinct managed Invoke URLs and gateway API keys are recorded securely, not in Git.
- [ ] Concierge has the five managed specialist base URLs and matching `AGENT_*_API_KEY`, its own inbound `CONCIERGE_CALL_KEY`/`CONCIERGE_APPROVAL_KEY`, specialist call/execute keys, and Core read/approval keys.
- [ ] Agent chat, approval continuation, execute endpoints and health function via deployed gateways; verify actual gateway path prefix and X-API-Key behavior.
- [ ] Each returned workflow ID survives a new customer turn and an interrupted approval flow.

## Banking portal / business correctness

- [ ] Portal is configured with Concierge's actual Invoke base URL and `CONCIERGE_API_KEY`; no specialist Invoke URLs are present in browser or BFF settings.
- [ ] Browser JavaScript contains no API keys, service capabilities, bearer tokens, or AgentID secrets.
- [ ] Demo login uses an HttpOnly session cookie, writes require CSRF, and the reset action uses a separate presenter code; use HTTPS + `DEMO_SECURE_COOKIE=true` on any shared host.
- [ ] The seed checking balance is R$8,450; the R$1,280 **credit-card** dispute does not debit checking or promise a refund.
- [ ] Card block and dispute each require independent consent, create persistent visible states, and can be safely retried after an ambiguous downstream response.
- [ ] Authorized R$1,250 Pix debits **exactly once**, checking becomes R$7,200 and the obligation-adjusted projection becomes R$5,100.
- [ ] Negative and ambiguous payment instructions do not create approval requests. Unknown/ambiguous cards, transactions and beneficiaries require clarification.
- [ ] English and Spanish natural-language receipts match the persisted transaction and account state; no raw JSON appears as the primary chatbot message.

## Observability and testing

- [ ] Audit-event correlation IDs are visible; label UI graph steps as *logical workflow activity* rather than platform traces.
- [ ] Real on-prem Agent Manager agent traces, MCP tool calls and permission denials are inspected in the platform telemetry UI; telemetry is not claimed without observed spans.
- [ ] Run `pytest -q`, `bash scripts/smoke-test.sh`, and **on synthetic local data only** `python scripts/integration-test.py`.
- [ ] Run optional Playwright browser tests and inspect the full customer journey after reset.

## Production exclusions

This is a synthetic VP demonstration. It is not a complete banking production reference: real OIDC customer authentication and step-up, regulated Pix, mTLS and hardening of managed service identities, durable enterprise session infrastructure, compliance/certification and infrastructure-specific secret rotation remain external concerns.
