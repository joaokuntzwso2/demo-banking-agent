# Banco Aurora — one-command supporting services + governed MCP publication

This launcher is designed for **WSO2 Agent Manager 1.0.0 on-premises** and the existing `demo-banking-agent` repository. It builds/deploys **PostgreSQL, Banking Core and the upstream Banking MCP server**, then publishes **one identity-secured Agent Manager MCP Proxy containing exactly 15 banking tools**. It also creates one OAuth scope per tool and six least-privilege roles. A Portal deployment is optional, after the Concierge is deployed.

**It does not build/deploy the six managed AI agents, attach MCP configurations to the agents, grant AgentID identities roles, create LLM providers/guardrails, or install Agent Manager itself.** These follow the manual Agent Manager setup runbook.

## Put the kit in your existing repository

```
demo-banking-agent/
  agents/
  mock_core/
  mcp_server/
  Dockerfile
  agent-manager-support/
    start.sh
    support.env.example
    compose.support.yaml
    k8s/
    publish_proxy.py
```

Do not run the scripts from a standalone Downloads directory: they need to build your Git checkout.

## One-time setup (same k3d cluster as Agent Manager — recommended)

Prerequisites: a running Agent Manager v1.0.0 and its egress gateway; `docker`, `kubectl`, `k3d`, Python 3, and the WSO2 `amctl` CLI. `amctl` must already be authenticated. Log in once:

```bash
amctl login --url http://api.amp.localhost:8080
amctl context show
kubectl config current-context
k3d cluster list
```

Then:

```bash
cd demo-banking-agent/agent-manager-support
cp support.env.example support.env
chmod 600 support.env
nano support.env
```

For a local AMP quick-start cluster, fill:

```dotenv
DEPLOY_TARGET=kubernetes
KUBE_CONTEXT=k3d-amp-local          # replace with actual `kubectl config current-context`
K3D_CLUSTER=amp-local               # replace with actual `k3d cluster list`
IMAGE_DELIVERY=k3d
AURORA_IMAGE=aurora-banking-support:amp-v4
AMP_PUBLISH=true
AMP_ORG=default                    # replace with actual org
AMP_ENVIRONMENT_HANDLE=default     # replace with actual environment handle
AMP_ENVIRONMENT_UUID=             # auto-discovery attempted; set real UUID if needed
PORTAL_ENABLED=false
AURORA_MCP_UPSTREAM_URL=           # auto private DNS: http://aurora-mcp.<namespace>.svc.cluster.local:8101/mcp
```

The gateway **and** the Agent Manager control plane must be able to resolve/reach the upstream MCP address. Kubernetes `ClusterIP` DNS only works within the cluster. If Agent Manager or its gateway is elsewhere, supply a **privately routable TLS upstream URL** and configure `MCP_ALLOWED_HOSTS` to match the actual Host header reaching the upstream. The launcher cannot create DNS/TLS routing without information about your infrastructure.

## Start + automatically publish

```bash
./start.sh
```

What it does:

1. Creates `secrets.env` (file mode 0600) with random, persistent credentials; never overwrites existing values.
2. Builds the Banco Aurora image, imports it into your k3d cluster, and creates a private namespace, PostgreSQL PVC, DB, Banking Core, and upstream MCP service.
3. Waits for Kubernetes rollouts.
4. Uses the authenticated WSO2 `amctl api` to discover the tools from the actual upstream URL.
5. **Refuses publication if tool discovery is not exactly the same as the 15 tools in the source.**
6. Registers `aurora-banking` as an Agent Manager **OAuth identity-secured** MCP Proxy (or verifies the existing proxy's upstream URL).
7. Creates/verifies 15 dedicated OAuth tool scopes and creates the `aurora-account`, `aurora-card`, `aurora-dispute`, `aurora-pix`, `aurora-wellness`, `aurora-risk` roles.

The script prints the private Banking Core and MCP addresses. **Do not publish Core or the unprotected upstream MCP to the internet.** The managed MCP Proxy is the public policy-enforced endpoint.

Run safely again to reconcile code and restart the backend. Existing PostgreSQL storage and generated secrets are retained. Existing MCP Proxies are not overwritten if their upstream address differs.

### Verify

```bash
./start.sh status
amctl api /orgs/default/mcp-proxies/aurora-banking
amctl api /orgs/default/mcp-proxies/aurora-banking/scopes
```

In Agent Manager Console: **Organization → Resources → MCP Servers**, open **Banco Aurora Banking MCP**, verify the environment deployment is **Active**, and confirm the 15 discovered tools are all covered by OAuth scopes.

## Add the six agents later

Agent Manager remains manual. When each specialist is deployed:

- Attach the organization-level **Banco Aurora Banking MCP** proxy in the specialist's **Tool Configurations**.
- Name the injected URL `MCP_SERVER_URL` and select **AgentID OAuth** authentication in your agent environment (`MCP_AUTH_MODE=agentid`).
- In **Agent Identities → Roles**, assign exactly one matching `aurora-*` role to each deployed specialist. Concierge receives **no** banking MCP role.
- Concierge needs the private Banking Core URL (printed by launcher), `BANK_CORE_READ_KEY` and `BANK_APPROVAL_KEY` (from your private `secrets.env`), and its own specialist invoke URLs and application credentials. Do not paste secrets into Git.

**Important:** Roles are created by the launcher but **not assigned to agents**. Assign them after the managed agent identities exist. Do not run a money-movement scenario before verifying a gateway-level read-allowed/write-denied request for the Account identity.

## Connect the portal to the managed Concierge

After deploying Concierge and creating its gateway API key, edit the **private** `secrets.env` and set:

```dotenv
CONCIERGE_URL=https://YOUR_AGENT_MANAGER_CONCIERGE_INVOKE_BASE
CONCIERGE_API_KEY=YOUR_GATEWAY_KEY
```

The file already contains `CONCIERGE_CALL_KEY`, `CONCIERGE_APPROVAL_KEY`, Core secrets and presenter codes. Set **the same Concierge application keys** on the managed Concierge. Then edit `support.env`:

```dotenv
PORTAL_ENABLED=true
```

Re-run the same launcher:

```bash
./start.sh
./start.sh portal
```

Open **http://localhost:8300** while `kubectl port-forward` runs. The browser only talks to the Portal BFF; the BFF calls the managed Concierge. Never put an API key in browser JavaScript.

With a public HTTPS ingress, configure `DEMO_SECURE_COOKIE=true` and your `DEMO_PUBLIC_ORIGIN` in the Portal, and use an authenticated access boundary; this simple kit defaults to localhost port-forward access only.

## Alternative: Docker local supporting services

In `support.env`:

```dotenv
DEPLOY_TARGET=docker
AMP_PUBLISH=false
```

Run `./start.sh`; it creates isolated services on `127.0.0.1` ports **8110** (Core) and **8111** (upstream MCP), and optional **8330** (Portal) without affecting your existing local test stack. Your remote Agent Manager cannot use `localhost` as an upstream. To publish from Docker, deploy a *private, secure* reverse proxy/ingress and set `AURORA_MCP_UPSTREAM_URL` to the reachable backend `/mcp` URL; configure the matching MCP Host allowlist and switch `AMP_PUBLISH=true`. Never use an unprotected public tunnel for the upstream: these synthetic tools include financial mutations.

## Other commands

```bash
./start.sh dry-run   # source inventory checks; no cluster/proxy changes
./start.sh status    # pods/services or Docker containers
./start.sh portal    # Kubernetes localhost port-forward of the UI
./start.sh stop      # stop workloads, KEEP database storage and MCP registration
```

The kit retains `secrets.env` and any PVC/volume when stopped. It does **not** erase the dataset, clean up the platform MCP Proxy, or delete agent credentials. The full write-capable banking scenario test (`scripts/verify-v4.sh --full` in the base repository) resets synthetic bank records; the launcher itself does not.

## Important limitations

- The actual WSO2 on-prem control plane and gateway are not available in this environment, so these automation calls are based on the **official v1.0.0 `amctl api` contracts** and have not been run against your installation. Expect environment UUID, cluster networking and gateway readiness to need verification.
- Image delivery `k3d` works only when your `k3d` command targets the same Docker host as your AMP cluster. For another Kubernetes distribution choose `IMAGE_DELIVERY=registry`, set a pullable `AURORA_IMAGE`, optionally `BUILD_AND_PUSH=true`, and supply `KUBE_CONTEXT`.
- The managed proxy must be marked Active in the Console. A successful API creation response alone does not mean the AI gateway is serving it.
- Scope creation is not a single atomic platform transaction. Run on a fresh proxy before attaching agents, verify all scopes, then assign roles. Unmapped tools can be default-permitted to authenticated clients.
- Treat this demo infrastructure as **synthetic demo only**. Database, session, and access patterns are not a production banking reference architecture.

## Official WSO2 sources

- [Register an MCP Proxy](https://wso2.com/agent-platform/docs/v1.0.0/guides/register-mcp-proxy/)
- [Authorize Agent Access to MCP Tools](https://wso2.com/agent-platform/docs/v1.0.0/guides/authorize-agent-access-to-mcp-tools/)
- [Configure MCP Proxies for an Agent](https://wso2.com/agent-platform/docs/v1.0.0/guides/configure-agent-mcp-proxies/)
- [CLI Installation](https://wso2.com/agent-platform/docs/v1.0.0/guides/cli-installation/)
