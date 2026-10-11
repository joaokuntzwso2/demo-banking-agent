# Banco Aurora — secure tunnel publisher environment override fix

## Root cause

The public tunnel launcher correctly exports `AURORA_MCP_UPSTREAM_URL=https://…trycloudflare.com/mcp` together with the secret `AURORA_MCP_ORIGIN_KEY`. However, `agent-manager-support/start.sh` then sources `support.env` **after** receiving the command environment and overwrites that public URL with the persisted internal HTTP Kubernetes URL. `publish_proxy.py` deliberately refuses that insecure combination.

## Apply to the existing Git working tree

From the repository root:

```bash
unzip -o ~/Downloads/Banco-Aurora-MCP-Env-Override-Fix.zip
bash -n agent-manager-support/start.sh
python3 -m unittest discover -s agent-manager-support/tests -v
./scripts/publish-mcp-secure-tunnel.sh status
./scripts/publish-mcp-secure-tunnel.sh publish
```

This fix changes only `agent-manager-support/start.sh` and adds one regression test file. It **does not** change `support.env`, the origin key, the tunnel hostname, Kubernetes workloads, or Agent Manager configuration. The live publisher still validates the authenticated public endpoint and exact 15 MCP tool descriptors before registration. Environment overrides are authoritative only for the four short-lived tunnel-related values: `AURORA_MCP_UPSTREAM_URL`, `AURORA_MCP_DISCOVERY_URL`, `AURORA_MCP_ORIGIN_KEY`, and `AMP_DISCOVERY_MODE`; other settings continue to load from `support.env`.

The origin key is never printed by the fix. The HTTPS tunnel is temporary: keep it alive for the demo and do not commit secrets or tunnel state. Because the hostname changes on tunnel restart, later proxy URL reconciliation may be required in the Console.

## Verify registered proxy

Only after `publish` completes without errors:

```bash
amctl api /orgs/default/mcp-proxies/aurora-banking | python3 -m json.tool
amctl api /orgs/default/mcp-proxies/aurora-banking/scopes | python3 -m json.tool
```

Check the managed proxy's Active status, OAuth enforcement, 15 scope-to-tool mappings, and its actual gateway invocation before relying on it for an executive demo. The source-level regression suite cannot establish live platform behavior.
