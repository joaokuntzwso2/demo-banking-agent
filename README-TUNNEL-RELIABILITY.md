# Banco Aurora — MCP Tunnel Reachability Repair (v2)

This overlay fixes **public tunnel reachability diagnostics** for the previously supplied `publish-mcp-secure-tunnel.sh`. It does **not** redeploy Core, MCP or PostgreSQL. It will not remove or bypass Agent Manager 1.0.0's private-address/SSRF validation.

## Why it is needed

The previous command received HTTP status `000` from `curl`, then killed its processes. `000` is not a server status; it means no HTTP response was received. We cannot attribute it to a particular cause without the curl and cloudflared error logs.

Changes:

* Retains `cloudflared`, bridge and port-forward logs after a failed start.
* Adds `diagnose` for a concise report of curl, origin and cloudflared errors.
* Waits for public DNS/TLS/tunnel readiness using bounded retries (40 attempts by default; override with `AURORA_PUBLIC_PROBE_ATTEMPTS`, range 1–120).
* Uses configured public HTTPS proxy settings if present (the earlier publisher forcibly skipped proxies during retry).
* Attempts normal-TLS DNS-over-HTTPS resolution if the public hostname is not yet resolvable and this curl supports DoH.
* Refuses TLS verification bypasses and requires both HTTP **401** and `X-Aurora-Origin-Auth: required`, a header emitted only by the authenticated Banco Aurora origin. An unrelated edge 401 does not count.
* Does not register the MCP proxy until the verified public reachability/security probe succeeds. It preserves the existing private origin authentication secret.

## Installation (from repository root)

```bash
unzip -o ~/Downloads/Banco-Aurora-MCP-Tunnel-Reliability-v2.zip
chmod +x scripts/publish-mcp-secure-tunnel.sh
bash -n scripts/publish-mcp-secure-tunnel.sh
python3 -m unittest discover -s agent-manager-support/tests -v
```

First, review *existing* logs from the prior failed attempt:

```bash
./scripts/publish-mcp-secure-tunnel.sh diagnose
```

Restart the temporary tunnel and attempt publication:

```bash
./scripts/publish-mcp-secure-tunnel.sh up
```

If it still fails, run:

```bash
./scripts/publish-mcp-secure-tunnel.sh diagnose
```

Send only the diagnostic lines and the failing `curl` error. **Do not disclose `agent-manager-support/.mcp-public-tunnel/origin.key` or environment secrets.**

If the script publishes successfully, verify both proxy and scopes:

```bash
amctl api /orgs/default/mcp-proxies/aurora-banking | python3 -m json.tool
amctl api /orgs/default/mcp-proxies/aurora-banking/scopes | python3 -m json.tool
```

## HTTPS network causes

- curl exit 6: DNS resolution failed. The new script retries, optionally with DNS-over-HTTPS.
- curl exit 28: connection timed out. Inspect `cloudflared.log`; check outbound port 7844 TCP/UDP and local networking.
- curl exit 35 or 60: TLS handshake/certificate issue. No `-k`/`--insecure` bypass permitted.
- HTTP 502/530: Cloudflare tunnel edge is reachable but the route/origin is unavailable or not registered yet.
- HTTP 401 without the Aurora response marker: some intermediate layer replied; do not consider the bridge verified.
- HTTP 401 **with** the Aurora response marker: the public request reached the authenticated origin and was denied as expected.

## Important: suitable for diagnosis, not the final executive demo

Cloudflare's **Quick Tunnel** documentation says Quick Tunnels are for development and do not support Server-Sent Events (SSE). Some MCP Streamable HTTP sessions require SSE, so passing this 401 probe is **not sufficient** to prove MCP interoperability through a Quick Tunnel. For the executive demo, use a **named Cloudflare Tunnel with a stable HTTPS hostname** (and the same authenticated origin) or another properly secured, supported external ingress. Test actual MCP initialize, tools/list and tools/call through Agent Manager after the final proxy is registered.

Official references:
- https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/
- https://developers.cloudflare.com/tunnel/troubleshooting/
- https://developers.cloudflare.com/tunnel/get-started/
