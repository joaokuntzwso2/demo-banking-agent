# Banco Aurora — MCP Governance Matrix

**15 purpose-specific Banking MCP tools; no broad `get_banking_snapshot` exposed.**

The authoritative up-to-date scope/role matrix, manual WSO2 steps and denial test are in [PLATFORM-SCENARIOS.md](PLATFORM-SCENARIOS.md#A-mcp-proxy--gateway-oauth-and-tool-permissions). Register an OAuth MCP Proxy and cover **every tool with a scope** (uncovered tools can be default-permit to authenticated agents). API-key-only access authenticates the caller but does not provide tool-specific identity authorization.

`agents/common.py::TOOLS_BY_ROLE` is an application allowlist for defense in depth, **not proof** that WSO2 gateway policy is installed. The separate Core validates customer ownership, immutable approval details, balance, recipient verification and idempotency. Concierge itself has **no Banking MCP permissions**.

Separate secrets: Portal→Concierge `CONCIERGE_CALL_KEY`/`CONCIERGE_APPROVAL_KEY`; Concierge→specialists `SPECIALIST_CALL_KEY`/`SPECIALIST_EXECUTION_KEY`; MCP→Core read/write keys; operator-only Core `BANK_CASE_OPERATOR_KEY`; managed gateway invoke API keys; AgentID OAuth for MCP.

The **negative gateway test** is mandatory: with Account Agent's valid identity and proxy URL, a permitted account read succeeds, while `execute_approved_pix` must be denied by **the actual Agent Manager gateway**. A local service error is not a substitute.
