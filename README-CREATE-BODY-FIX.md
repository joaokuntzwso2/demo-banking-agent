# Banco Aurora MCP Proxy — Agent Manager 1.0.0 create-body fix

## What failed

`amctl api /orgs/default/mcp-proxies -X POST --input -` returned HTTP 400 `Invalid request body` **after** live discovery verified all 15 tools.

The prior publisher submitted `"capabilities":{"tools":["get_account_balance", ...]}` — a JSON **array of strings**. Agent Manager's published Go model `MCPProxyCapabilities.Tools` is `[]map[string]interface{}` and expects each tool as a **JSON object**, complete with its `name`, genuine `inputSchema`, and optional `description`/other MCP metadata. The earlier name-only list is an incompatible JSON type.

This patch captures full tool descriptors from a genuine `MCP tools/list` request using `tool.model_dump(mode="json", by_alias=True, exclude_none=True)` and passes them to the create API. It validates that all 15 expected names are present, unique, and include genuine JSON-object schemas. It will not invent permissive schemas.

References:
- https://pkg.go.dev/github.com/wso2/agent-manager/agent-manager-service/models (MCPProxyCapabilities)
- https://wso2.github.io/agent-manager/docs/v1.0.0/guides/authorize-agent-access-to-mcp-tools/

## Install

From your `demo-banking-agent` repository root:

```bash
unzip -o ~/Downloads/Banco-Aurora-MCP-Create-Body-Fix.zip
python3 -m unittest discover -s agent-manager-support/tests -v
bash -n agent-manager-support/start.sh
```

The zip only modifies `agent-manager-support/publish_proxy.py` and its relevant tests. It does not modify services, your database, `support.env`, or Agent Manager.

## Publish only (no workload restart)

```bash
AMP_DISCOVERY_MODE=cluster bash agent-manager-support/start.sh publish
```

The patch is tested locally against 15 mocked regression tests but has not been run against **your** Agent Manager instance. If Agent Manager now rejects the private URL on the create API itself, do not disable its SSRF checks: use a vendor-supported private endpoint configuration or securely exposed HTTPS ingress. If it reports another body error, collect backend validation details:

```bash
kubectl -n wso2-amp logs deployment/amp-api --since=5m --tail=150 \
  | grep -Ei 'mcp|proxy|invalid|request|error' | tail -80
```

Do not share authentication tokens, credentials, or raw request bodies containing secrets.

## Verification

```bash
amctl api /orgs/default/mcp-proxies/aurora-banking | python3 -m json.tool
amctl api /orgs/default/mcp-proxies/aurora-banking/scopes | python3 -m json.tool
kubectl -n aurora-demo get pods,svc
```

The proxy is NOT ready until creation, all 15 scopes, and correct tool-to-scope assignments succeed, and its gateway artifact shows `Deployed` with OAuth enabled. AgentID role assignments remain manual.
