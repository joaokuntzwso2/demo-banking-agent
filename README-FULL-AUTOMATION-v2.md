# Banco Aurora — comprehensive startup launcher, revision 2

Apply this **overlay** to the existing `demo-banking-agent` repository. It is
not a standalone copy of the banking application.

```bash
cd /Users/joaoluiskuntz/wso2mi/Projects/Demo/demo-banking-agent
unzip -o ~/Downloads/Banco-Aurora-Full-Stack-Automation-v2.zip
chmod +x scripts/start-everything.sh scripts/publish-mcp-secure-tunnel.sh
./scripts/start-everything.sh up
```

On a healthy repeated startup, Colima, k3d, Agent Manager, backing deployments,
public tunnel, and registered MCP Proxy are **reused**. The tunnel's `up`
command is deliberately idempotent, not a forced restart. If some launcher-owned
processes have failed, only that local tunnel tier is recovered. Runtime code
changes trigger a backend rebuild through an application fingerprint.

The orchestrator verifies that the 15-tool proxy has AgentID security and
one-tool OAuth scopes, then attempts to create, build, bind, and deploy five
specialists and Concierge. It refreshes the newly issued specialist credentials
before configuring Concierge, and attempts the Portal and end-to-end scenarios.

**This archive has 52 passing offline tests, not a verified live six-agent
deployment**. The runtime Agent Manager 1.0.0 REST paths for agent tool binding
and gateway credentials may still need release-specific adjustment, and an
actual negative OAuth gateway test is mandatory. The launcher fails clearly
rather than claiming success on unverified operations.

When Quick Tunnel rotates, the launcher tries a guarded `PUT` on the existing
exact `aurora-banking` proxy. It never deletes a proxy, changes another proxy,
or disables platform URL security. If Agent Manager rejects that update, the
script stops and reports the server's error; a stable named HTTPS tunnel is
more reliable for demonstrations.

**Useful commands:**

```bash
./scripts/start-everything.sh status
./scripts/start-everything.sh up
AURORA_REFRESH_BACKEND=1 ./scripts/start-everything.sh up
./scripts/publish-mcp-secure-tunnel.sh status
./scripts/publish-mcp-secure-tunnel.sh diagnose
./scripts/publish-mcp-secure-tunnel.sh ensure
./scripts/start-everything.sh check
```

Never commit `agent-manager-support/support.env`,
`agent-manager-support/secrets.env`, or `.mcp-public-tunnel` state.

Full details: `docs/ONE-COMMAND-FULL-STACK.md`.
