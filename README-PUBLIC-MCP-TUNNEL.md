# Banco Aurora — authenticated public HTTPS upstream for AMP 1.0.0

## Why this patch exists

The AMP control plane has rejected `http://aurora-mcp.aurora-demo.svc.cluster.local:8101/mcp` twice:

- `fetch-server-info`: `url host resolves to a non-public IP address`
- `POST /orgs/default/mcp-proxies`: `endpoint "primary" upstream url: url host resolves to a non-public IP address`

This is a server-side SSRF protection. Do **not** disable it, spoof DNS, or create a public hostname resolving to a private address. The correct upstream address must resolve publicly and be reachable via TLS.

This patch is intended **only for a local synthetic demo** and uses Cloudflare Quick Tunnel for a temporary HTTPS hostname. It adds a real application-layer secret check **before** proxying to the private Kubernetes MCP. No secrets are printed. WSO2's MCP Proxy is configured to send the secret as a documented upstream API-key authentication header `X-Aurora-Origin-Key` (Go `UpstreamEndpoint.Auth`). Its inbound WSO2 OAuth and scope governance are kept separate.

## Install into your existing Git checkout

At repo root:

```bash
unzip -o ~/Downloads/Banco-Aurora-MCP-Public-Tunnel-Fix.zip
chmod +x scripts/publish-mcp-secure-tunnel.sh
brew install cloudflared  # only if command -v cloudflared fails
bash -n scripts/publish-mcp-secure-tunnel.sh
python3 -m unittest discover -s agent-manager-support/tests -v
```

Prerequisites: `kubectl` on `k3d-amp-local`, a working Colima `agent-manager` profile, `amctl` logged into the correct AMP org, `cloudflared`, and healthy `aurora-mcp` deployment. No Docker rebuild or Kubernetes manifest changes needed.

## One command

```bash
./scripts/publish-mcp-secure-tunnel.sh up
```

The launcher:

1. Verifies the existing `aurora-mcp` deployment without restarting it.
2. Starts a Kubernetes port-forward, loopback-bound: `127.0.0.1:18101` -> MCP 8101.
3. Starts an authenticated local proxy, loopback-bound: `127.0.0.1:18102`; only `/mcp` and supported methods are forwarded.
4. Generates and saves a random 64-ish-character key in `agent-manager-support/.mcp-public-tunnel/origin.key` (0600, gitignored); never writes it to `support.env` or the Git repo.
5. Starts a Cloudflare Quick Tunnel to the *authenticated local proxy* and waits for a temporary `https://random.trycloudflare.com` hostname.
6. Verifies anonymous `GET /mcp` receives `401` **from the actual public endpoint**. Fails closed if the endpoint is unprotected/unreachable.
7. Uses already-working, private *in-cluster* MCP tool discovery to retrieve 15 real tool descriptors.
8. Registers Agent Manager MCP Proxy with `upstream.main.url=https://.../mcp` and `upstream.main.auth={type:api-key,header:X-Aurora-Origin-Key,value:<secret>}`.
9. Attempts 15 tool scopes and six AgentID roles. Assign the roles to the managed agents manually later.

**Important:** Keep the tunnel running while Agent Manager uses the MCP proxy. It is backed by processes on your Mac. The temporary URL expires when you stop it or Cloudflare disconnects. After restarting, a *new hostname* cannot be silently written over a previously registered MCP Proxy; the publisher deliberately detects that. To use a long-lived executive demo, prefer a **Cloudflare named tunnel on a domain you control with a stable HTTPS hostname** and the same authenticated origin design. Then edit the MCP Proxy's endpoint through the Console with the new stable URL and origin header.

## Commands

```bash
./scripts/publish-mcp-secure-tunnel.sh status
./scripts/publish-mcp-secure-tunnel.sh publish    # retry creation if the tunnel is still running
./scripts/publish-mcp-secure-tunnel.sh stop       # removes only the three tunnel/forwarder processes
```

The script will never delete the cluster, database, namespaces, or managed agents. It does not replace `agent-manager-support/start.sh` normal deployment mode.

## Verify publication

```bash
amctl api /orgs/default/mcp-proxies/aurora-banking | python3 -m json.tool
amctl api /orgs/default/mcp-proxies/aurora-banking/scopes | python3 -m json.tool
```

Also inspect the Agent Manager Console: MCP Proxy deployed, upstream HTTPS, OAuth inbound, 15 scopes and six role definitions. A successful create response alone does not prove the AI Gateway is reachable; run an end-to-end, identity-scoped MCP call before the executive demonstration.

## Threat model and limitations

- Never expose Kubernetes port 8101 directly or forward from `0.0.0.0`.
- Do not run an unauthenticated Cloudflare Quick Tunnel to `aurora-mcp` or expose the Banking Core, PostgreSQL or Portal through this tunnel.
- The random public hostname is discoverable; **only the separate unpredictable origin key** prevents direct invocation. Anyone with that key can reach the upstream toolset, so protect and rotate it.
- Cloudflare Quick Tunnels are documented for testing/development, not production. If there is an organizational restriction on third-party tunnels or handling even synthetic financial data, do not use this approach. Deploy a vendor-supported private-endpoint allowance or a customer-owned, properly secured public HTTPS reverse proxy instead.
- The local stdlib bridge streams Streamable HTTP replies; validate actual MCP `initialize`, `tools/list`, and `tools/call` over the deployed AI Gateway. Network interruptions, proxy buffering, and SSE timeouts remain integration risks.
- This package is source-level and simulated HTTP tested only. It has **not** been executed against your Mac/Cloudflare/AMP cluster and cannot guarantee AMP deployment until live verification.

WSO2 references: https://wso2.github.io/agent-manager/docs/v1.0.0/guides/register-mcp-proxy/ ; https://wso2.com/agent-platform/docs/v1.0.0/guides/authorize-agent-access-to-mcp-tools/
Cloudflare reference: https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/
