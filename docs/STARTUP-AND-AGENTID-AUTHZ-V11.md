# Banco Aurora v11 — native AgentID denial + first-run tunnel recovery

## Scope

This additive package addresses the strict gate observed after **S01–S06 passed**:
`gateway-level unauthorized AgentID MCP tool-call proof not configured`.

It also adjusts `start-everything.sh up`, `agents`, and `check` so that a live HTTPS tunnel
is checked rather than trusting `cloudflared` PID liveness. An expired Quick Tunnel is
stopped and recreated using the project's existing, previously exercised
`publish-mcp-secure-tunnel.sh stop/up` interface with
`AURORA_ALLOW_EPHEMERAL_PROXY_UPDATE=true`. The publisher remains responsible for
safely matching and updating **only** the existing Banco Aurora MCP proxy.

**No commit, push, build, deployment, token write, role mutation, or tunnel change occurs during installation.**
The updated `up` flow does perform its normal controlled deployment/reconciliation as needed.

## Installation (existing checkout)

1. Inspect the reviewed local files before applying. The patcher refuses unfamiliar anchors:

   ```bash
   cd /Users/joaoluiskuntz/wso2mi/Projects/Demo/demo-banking-agent
   unzip -o ~/Downloads/banco-aurora-startup-agentid-v11.zip -d .
   python3 scripts/patch-aurora-startup-v11.py
   ```

2. Apply only after the inspection reports `PATCH REQUIRED`:

   ```bash
   python3 scripts/patch-aurora-startup-v11.py --apply
   bash -n scripts/start-everything.sh
   python3 -m py_compile scripts/aurora-full-reconcile.py \
       scripts/verify-aurora-tunnel-public.py \
       scripts/verify-aurora-agentid-denial.py
   python3 -m unittest discover -s tests -p 'test_startup_authz_v11.py' -v
   git diff --check
   git diff -- scripts/start-everything.sh scripts/aurora-full-reconcile.py
   ```

Backups of changed files are written below
`agent-manager-support/.source-sync-backups/startup-*.before-v11` (not in source paths).

## Validate now (without rebuilding)

```bash
python3 scripts/verify-aurora-tunnel-public.py
python3 scripts/verify-aurora-agentid-denial.py
python3 scripts/verify-aurora-agentid-denial.py --run
./scripts/start-everything.sh check
```

* The public tunnel check reads the stored URL, obtains an unauthenticated 401
  from the private authenticated bridge on localhost:18102 and the public HTTPS
  endpoint, and requires the bridge marker headers to match. It does not send
  any key to Cloudflare. A WAF-generated 401 without matching marker is rejected.
* The default AgentID invocation performs read-only Kubernetes inventory and
  Secret-reference checks. `--run` temporarily attaches an isolated Python
  diagnostic container to the **existing aurora-account Pod**, feeds the live
  Secret-backed OAuth credentials only via stdin, mints a token using the
  injected token endpoint and `resource=MCP_SERVER_URL`, checks that the token
  carries `aurora-banking:balance-read` but **not** `aurora-banking:card-read`,
  and initializes a real MCP session. It requires a successful read-only
  `get_account_balance` tool call followed by **HTTP 403** on read-only
  `get_card_status` with the same identity/token. `401`, `404`, `200` or an
  application-level role check do **not** count as a pass. There are **no
  state-changing banking tool calls** in this proof.
* The ephemeral container remains in the pod specification until pod replacement.
  A running previously attached `aurora-authz-*` container may be reused.
* The process does not reveal credentials, access tokens, full endpoints, or
  customer response bodies. It may require permission for ephemeral containers
  and pulling `python:3.12-alpine` if not already cached.
* If your Thunder instance uses opaque tokens rather than JWTs, the native
  proof fails closed at `token_scope_unreadable` until a safe scope-inspection
  contract is established. It will **not** claim security verified from an
  uninspectable token.

### Single-command bootstrap / repeat run

```bash
unset AURORA_AGENT_SOURCE_PUBLISH
./scripts/start-everything.sh up
```

- The script reuses existing cluster/backends/builds and only reconciles when needed.
- Stage 3 performs the marker health test, stops only expired *owned* tunnel
  processes, runs controlled `up`, verifies the marker again, then checks the
  MCP proxy and exact 15 scopes.
- Stage 5 reconciles builds/agents (remote Git must already match committed
  source).
- Stage 6 checks S01–S06 and then executes the native AgentID proof.
- `./scripts/start-everything.sh agents` now also ensures the MCP tunnel is
  verified/repaired first, avoiding the previously observed false-positive
  `MCP Proxy Deployed` while the Cloudflare tunnel was expired.
- `./scripts/start-everything.sh check` is intentionally read-only about the
  tunnel: it reports stale public HTTPS and directs you to `up` for repair.

**Cold-start external prerequisites cannot be bypassed**: the repository's
Agent Manager login (OAuth), configured governed OpenAI upstream credential,
correct Git publication, working Cloudflare connectivity, environment RBAC,
and platform-side AgentID injection must exist or be provisionable by the
original scripts. Quick Tunnels have no uptime guarantees. A stable managed
Cloudflare named tunnel is recommended for repeatable demos.

### Limitations and troubleshooting

- `MCP_TUNNEL_PUBLIC=not_verified`: examine bridge marker support and HTTPS;
  do not waive the check. The public check intentionally rejects a generic
  HTTP 401 without an originating-bridge marker.
- `AGENTID_GATEWAY_DENIAL=not_verified`: use the printed failure **stage and
  HTTP status**, not private env or raw logs. If HTTP 200 is returned on
  `get_card_status`, do **not** approve further demo financial actions until
  the missing gateway permission is corrected. If the token is opaque, review
  the Thunder token introspection contract rather than skipping validation.
- To restore sources, copy the backed-up files from
  `agent-manager-support/.source-sync-backups` into `scripts` after reviewing
  the diff. This does not undo any unrelated Kubernetes changes.

WSO2 1.0.0 references:
- https://wso2.com/agent-platform/docs/v1.0.0/guides/authorize-agent-access-to-mcp-tools/
- https://wso2.com/agent-platform/docs/v1.0.0/guides/configure-agent-mcp-proxies/
