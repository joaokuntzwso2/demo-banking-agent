# Optional external risk agent

Run as a normal container/VM service, then register its HTTPS `/chat` endpoint in
WSO2 Agent Manager as **Externally-Hosted**. Requires governed MCP Proxy
`MCP_SERVER_URL` + `MCP_AUTH_MODE=apikey` and `MCP_PROXY_API_KEY`, or external
AgentID client credentials. Grant **only `list_beneficiaries`** tool scope.
Configure `AGENT_RISK_URL` and `AGENT_RISK_API_KEY` in Concierge. All decisions
are advisory; Pix and the Banking Core enforce recipient verification.
