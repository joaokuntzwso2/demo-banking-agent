# aurora-concierge

Self-contained Git build context for WSO2 Agent Manager 1.0.0.

- **Source type:** Platform-Hosted → Source Code
- **Project Path:** `/deployment/agent-manager/apps/aurora-concierge`
- **Runtime:** Python 3.12; start command `python main.py`
- **Interface:** Custom API Agent on port **8000**, OpenAPI path `/openapi.json`
- **Endpoints:** `POST /chat`, GET `/health`, POST `/approve`, GET `/workflow/{workflow_id}`
- Read root README for environment variables, MCP Proxy, identity, gateway keys, and workflow.

**Generated source.** Change the canonical modules at repository root and rerun
`python deployment/agent-manager/scripts/prepare-apps.py`; commit the regenerated files before building agents.
