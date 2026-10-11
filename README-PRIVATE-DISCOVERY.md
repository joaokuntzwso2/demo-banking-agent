# Banco Aurora — In-cluster MCP discovery for local AMP v1.0.0

## Why this patch exists

The running `aurora-mcp` service has a private in-cluster URL:

`http://aurora-mcp.aurora-demo.svc.cluster.local:8101/mcp`

Agent Manager 1.0.0 refuses the `fetch-server-info` call for a hostname resolving to a non-public IP. This is a server-side security boundary, not an unhealthy MCP service or an incorrect environment UUID.

WSO2's [documented `amctl` workflow](https://wso2.com/agent-platform/docs/v1.0.0/guides/authorize-agent-access-to-mcp-tools/) also permits `POST /orgs/<org>/mcp-proxies` with the audited `capabilities.tools` list as part of the proxy payload.

This opt-in publisher mode performs **live MCP protocol discovery inside the already-running Kubernetes MCP pod** rather than directing the Agent Manager control plane to fetch a private address. It then uses the normal `amctl` proxy creation, scope creation, and role creation APIs. It does not disable or reconfigure any Agent Manager SSRF defenses. It is restricted to the exact Banco Aurora service hostname, namespace, port and path.

## Apply (no restart or rebuild)

From the existing project root:

```bash
cd /Users/joaoluiskuntz/wso2mi/Projects/Demo/demo-banking-agent
unzip -o ~/Downloads/Banco-Aurora-MCP-Private-Discovery-Fix.zip
bash -n agent-manager-support/start.sh
python3 -m unittest discover -s agent-manager-support/tests -v
```

Your `support.env`, `secrets.env`, Kubernetes secrets, Postgres PVC and Docker/Colima installation are not included.

## Run only the MCP publication step

```bash
AMP_DISCOVERY_MODE=cluster bash agent-manager-support/start.sh publish
```

Alternatively, add `AMP_DISCOVERY_MODE=cluster` to the private `agent-manager-support/support.env` so subsequent runs use the verified in-cluster method.

Success should print **15 tools verified**, successful `aurora-banking` proxy creation, **15 OAuth scopes**, and creation or verification of six agent roles. Inspect the result:

```bash
amctl api /orgs/default/mcp-proxies/aurora-banking | python3 -m json.tool
amctl api /orgs/default/mcp-proxies/aurora-banking/scopes | python3 -m json.tool
amctl api /orgs/default/environments/default/agent-identities/roles | python3 -m json.tool
```

The last command may include roles unrelated to Banco Aurora; inspect `aurora-` roles. Also verify the proxy environment's `Deployed` status and `OAuth` security in the Console.

## Boundaries and possible next blocker

- The code **does not weaken the API's SSRF check** or trick DNS into resolving to a different host.
- The in-cluster inventory is obtained via MCP `initialize` + `tools/list` from the live server, not merely source scanning. Both inventories must match **all 15** tool names.
- This is appropriate only for the local demo cluster you control and its named `aurora-mcp` deployment.
- **Not verified against your running AMP control plane yet.** If AMP also rejects private endpoints on the actual `POST /mcp-proxies`, then creation will still fail. In that case use a vendor-supported private-upstream allowlist/configuration or a suitably secured HTTPS ingress address that AMP can resolve and reach; do not expose the raw banking MCP to the public internet or disable SSRF protections.
- No agent is deployed or assigned an identity by this patch; those remain manual.
