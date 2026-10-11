# Banco Aurora — Agent Manager MCP Binding v9

## Problem fixed

Agent Manager v1.0.0 returned a summary for `GET /.../tool-configs` or `GET /.../mcp-configs`: the list item contains `uuid`, `name`, and `type`, but deliberately omits `environmentVariables`.

The v8 reconciler incorrectly read `environmentVariables` from that list item immediately after successfully creating the `aurora-banking` tool configuration, resulting in:

`ERROR: Agent aurora-account MCP tool config does not inject MCP_SERVER_URL`

## What changed

- For the unique `aurora-banking` MCP config, resolve its UUID from the list entry and perform `GET <detected configuration collection>/<uuid>`.
- Verify the returned detail has `type=mcp`, the expected proxy and UUID, and mappings `url -> MCP_SERVER_URL`, `apikey -> MCP_PROXY_API_KEY`.
- When the *verified detail* lacks a mapping, perform a narrow `PUT` update using only `environmentVariables` (the supported `UpdateAgentModelConfigRequest` field) and re-read the detail before accepting success.
- Reuse existing configurations. Never create a second one merely because list summaries lack variable mappings.
- Refuse writes on ambiguous config collections, duplicate entries, unexpected proxy/type, missing UUID, or unavailable detail endpoint.
- No Git operations; no GitHub Actions; no changes to `support.env`, `secrets.env`, Kubernetes, tunnel, or MCP scopes.

## Apply

From the root of your local `demo-banking-agent` checkout:

```bash
unzip -o ~/Downloads/Banco-Aurora-MCP-Tool-Binding-Fix-v9.zip
python3 -m py_compile scripts/aurora-full-reconcile.py
python3 -m unittest discover -s tests -p test_tool_binding_detail.py -v
unset AURORA_AGENT_SOURCE_PUBLISH
./scripts/start-everything.sh agents
```

If your tunnel changed or expired, recover it with `./scripts/publish-mcp-secure-tunnel.sh up` and then retry the agents command.

## Validation

The patch was tested offline with simulated Agent Manager API list/detail/update results. It is **not yet verified against your live Agent Manager installation**, so fail-closed errors remain possible if your installed server's read/update endpoints deviate from the documented v1.0.0 API.
