# Banco Aurora — MCP publisher recovery patch

## What failed

The Banco Aurora supporting services already deployed in namespace `aurora-demo` and were observed healthy. Publishing failed because `publish_proxy.py` did not unwrap the `environments` list in Agent Manager's response. A function-local `tmp` was also used in a shell EXIT trap, resulting in `tmp: unbound variable` after the function returned.

## Apply (from the Banco Aurora Git repository)

```bash
cd /Users/joaoluiskuntz/wso2mi/Projects/Demo/demo-banking-agent
unzip -o ~/Downloads/Banco-Aurora-MCP-Publisher-Fix.zip
bash -n agent-manager-support/start.sh
python3 -m py_compile agent-manager-support/publish_proxy.py
python3 -m unittest discover -s agent-manager-support/tests -v
```

Your `agent-manager-support/support.env`, `agent-manager-support/secrets.env`, `.env`, database and Colima profile are NOT included in the patch and are not touched by the commands above.

## Retry publication only

```bash
amctl context show
amctl api /orgs/default/environments | python3 -m json.tool
bash agent-manager-support/start.sh publish
```

`publish` checks that `aurora-core` and `aurora-mcp` are deployed and ready, reuses the existing private Kubernetes service address, then attempts Agent Manager MCP registration. It neither reapplies the Kubernetes manifest nor builds/imports images.

## If the environment UUID cannot be detected

Inspect the response from:

```bash
amctl api /orgs/default/environments | python3 -m json.tool
```

Use ONLY the UUID belonging to the `default` environment; do not use an organization or gateway UUID. Add to `agent-manager-support/support.env`:

```dotenv
AMP_ENVIRONMENT_UUID=<real-default-environment-uuid>
```

Then retry `bash agent-manager-support/start.sh publish`.

## Caveats

The patch fixes the local discovery and cleanup errors, but it has not been exercised against your running Agent Manager API. Later MCP API operations (capability discovery, create, OAuth security settings, tool scopes and AgentID role creation) have to be verified against the installed AMP 1.0.0 API. Don't describe the proxy as OAuth-governed until Security and per-tool scopes are confirmed in the Agent Manager Console.

No MCP proxy registration was confirmed in the supplied log. Don't push a Git commit or run full redeployment until the `publish` stage succeeds and you inspect the created proxy.
