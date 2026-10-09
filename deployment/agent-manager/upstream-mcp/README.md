# Banking MCP upstream

Deploy as a **normal Kubernetes/VM application**, not as one of the six Platform-Hosted agents. WSO2 Agent Manager registers its HTTPS `/mcp` endpoint as an MCP Proxy and exposes a governed environment-specific URL.

Docker build from this folder: `docker build -t aurora-banking-mcp:1.0 .`
Configure `BANK_CORE_URL`, `BANK_CORE_READ_KEY`, `BANK_CORE_WRITE_KEY`, `MCP_ALLOWED_HOSTS`, `MCP_ALLOWED_ORIGINS`. Restrict ingress so only the WSO2 gateway can reach the upstream service. No gateway tokens belong in this upstream process. Read root README for full instructions.
