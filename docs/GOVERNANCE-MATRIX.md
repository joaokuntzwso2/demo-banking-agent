# MCP Governance and Service-Security Matrix

This is a **manual on-premises WSO2 Agent Manager configuration plan**. Local Compose does not enforce WSO2 gateway OAuth policy. No claims below imply that scopes have already been installed on a real Agent Manager cluster.

## OAuth scope-to-tool plan

**All seven** tools must be covered by explicit scopes because uncovered tools can be default-permit to authenticated MCP callers. The resource handle below assumes an MCP Proxy named `aurora-banking`; adjust it to the actual registered handle.

| Tool | Cover with scope | Only grant to |
|---|---|---|
| get_banking_snapshot | `aurora-banking:read` | Account, Card, Dispute, Pix, Wellness |
| request_card_block | `aurora-banking:card-request` | Card |
| execute_approved_card_block | `aurora-banking:card-execute` | Card |
| request_dispute | `aurora-banking:dispute-request` | Dispute |
| execute_approved_dispute | `aurora-banking:dispute-execute` | Dispute |
| request_pix | `aurora-banking:pix-request` | Pix |
| execute_approved_pix | `aurora-banking:pix-execute` | Pix |

**Aurora Concierge receives no Banking MCP Proxy tool permission.** It orchestrates through independently protected specialist service endpoints.

Follow WSO2 Agent Manager v1.0.0 documentation: [register a proxy](https://wso2.com/agent-platform/docs/v1.0.0/guides/register-mcp-proxy/), [configure access](https://wso2.com/agent-platform/docs/v1.0.0/guides/authorize-agent-access-to-mcp-tools/), [attach to hosted agents](https://wso2.com/agent-platform/docs/v1.0.0/guides/configure-agent-mcp-proxies/). Use OAuth rather than only an API key where tool scopes matter.

## Service credentials, isolated by trust boundary

| Boundary | Secret name | Held by |
|---|---|---|
| Browser login (fake demo sign-in) | `DEMO_ACCESS_CODE` | BFF only (checked against human input) |
| Browser session integrity | `DEMO_SESSION_SIGNING_KEY` | BFF only |
| Presenter reset | `DEMO_RESET_CODE` | BFF only (checked against presenter input) |
| BFF -> Concierge chat | `CONCIERGE_CALL_KEY` | BFF + Concierge |
| BFF -> Concierge approval | `CONCIERGE_APPROVAL_KEY` | BFF + Concierge |
| Concierge -> Specialist read/chat | `SPECIALIST_CALL_KEY` | Concierge + all specialists |
| Concierge -> Specialist execute | `SPECIALIST_EXECUTION_KEY` | Concierge + three write specialists |
| BFF/Concierge/MCP -> Core read | `BANK_CORE_READ_KEY` | BFF, Concierge, Core, upstream MCP |
| Concierge -> Core approval/workflow | `BANK_APPROVAL_KEY` | Concierge + Core |
| MCP -> Core mutations | `BANK_CORE_WRITE_KEY` | Upstream MCP + Core |
| BFF -> Core demo reset | `BANK_RESET_KEY` | BFF + Core |
| Agent Manager invoke | `CONCIERGE_API_KEY` / `AGENT_*_API_KEY` | Relevant server-side caller, platform gateway |
| Specialist -> MCP OAuth | `AMP_AGENTID_*` | Injected by Agent Manager for each hosted agent |

**All values in checked-in `.env.example` and Compose are LOCAL-DEMO defaults, not safe shared-environment secrets.** On-prem deployment requires independently generated random secrets, TLS, authenticated ingress, private service networking, and appropriate runtime secret distribution.

## Threat scenarios and negative tests

1. Unauthenticated portal `/api/state`, `/api/chat`, `/api/approvals/*`: reject.
2. Authenticated browser request without CSRF: reject.
3. Account agent `execute_approved_pix` at WSO2 MCP Proxy: **gateway denies**, not just a Python error (run this on actual installation and capture the trace).
4. Unrecognized card charge without an explicit `block` or `dispute` command: no pending approval.
5. `Do not pay` or `How does Pix work?`: no payment approval.
6. Wrong customer/card/beneficiary/approval payload: Core rejects.
7. Retry completed payment with same approval: identical receipt, one debit.
8. Replay rejected approval as approved: Core rejects.
9. Browser cannot select `customer_id`; BFF binds the synthetic session to C001.
10. No real banking tokens, card secrets or external financial APIs in the demo.

For real customer data, replace the single-customer demo identity with bank OIDC and service-level user delegation (not implemented here). Human click approval is NOT MFA or regulatory step-up authentication.
