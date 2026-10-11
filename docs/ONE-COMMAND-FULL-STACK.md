# Banco Aurora — full-stack reconciliation launcher (WSO2 Agent Manager v1.0.0)

## What this script does

Run from the repository root:

```bash
./scripts/start-everything.sh up
```

The script invokes the already-tested repo launchers to (1) reuse/create the Colima + k3d + Agent Manager installation, (2) reconcile Banking Core, PostgreSQL and MCP, (3) reuse/create the authenticated Cloudflare HTTPS bridge and register `aurora-banking`, (4) verify its 15 tool descriptors and scopes, (5) create a project and six managed Custom API agents, build source from **published Git SHA** and deploy specialists before Concierge, and (6) assign five least-privilege AgentID roles and run final checks. Concierge does not receive banking MCP permissions. The optional external Risk Advisor remains a separate external application, not a seventh managed agent.

The supporting bank service's `support` action intentionally skips its original HTTP/private-address MCP registration; the secure tunnel owns publication. No cluster/PVC resets.

### Key facts that prevent pretending everything succeeded

Agent Manager v1.0.0 does **not** expose an `amctl` command that universally creates agent MCP tool configurations. The new Python reconciler probes the candidate REST resource and only sends a create request if a read-only probe identifies one unique route. It then re-reads the configuration and rejects a missing `MCP_SERVER_URL` injection. **That binding API payload has not been tested against your live Agent Manager**; the script may stop there. It never invents a success.

Platform-hosted API agents use gateway-issued `X-API-Key` credentials. These are not the banking application's `SPECIALIST_CALL_KEY` and `CONCIERGE_CALL_KEY`. Agent Manager shows gateway credentials only on creation and does not return them in a later read. The script cannot conjure them. The reconciler now tries to provision and persist six gateway credentials automatically, but **only** if it can uniquely identify the corresponding v1.0.0 API-key collection through successful read-only probes. It never invents keys and never creates a key on an unverified route. If none matches your installed runtime, the script stops with a precise message rather than trying speculative writes. Issued keys are stored once in private `agent-manager-support/secrets.env` (mode 600, Git-ignored). New Concierge agents receive specialist keys as secret env vars at creation. Existing Concierge agents with unverifiable key bindings are rejected; gateway keys are never sent as plaintext deployment env vars. Once an authentic Concierge gateway key and invoke endpoint exist, the script enables and deploys Portal automatically and runs the stateful S01–S06 suite through a temporary localhost port-forward. A stable LLM provider also needs a real provider credential; this script defaults to deterministic no-LLM mode rather than fabricating one.

Current `scripts/aurora-full-reconcile.py` is a **strict deploy/reconcile foundation**, not a verified fully unattended release. This distinction is deliberate. The final `check` stage automatically runs real Portal S01–S06 scenarios and also requires `AURORA_MCP_AUTHZ_VERIFY_CMD` proving gateway-level scope denial. Until native permitted/denied MCP calls and S01–S06 are validated, exit code is nonzero. Do not replace these checks with `true`.

### Prerequisites

macOS with Colima, Docker, k3d, kubectl, Python 3, Git, `amctl`, cloudflared; local Agent Manager login (the script can initiate login if absent). The script cannot bypass interactive OAuth. The six platform-hosted agents build from the public Git repository, so the source in `main` must match your local HEAD. The orchestrator deliberately requires the remote commit to match, otherwise it fails instead of building stale agent code.

Production-grade secrets should be provided through WSO2 managed secrets, not `--env-secret` process arguments. The current localhost example sends creation secrets as command arguments; they may be visible to other local users inspecting processes during creation. Never publish `agent-manager-support/secrets.env` or the tunnel origin key to Git.

## Actions

```bash
./scripts/start-everything.sh up      # full strict orchestration
./scripts/start-everything.sh agents  # reconcile managed agents on existing infrastructure
./scripts/start-everything.sh check   # strict verifier
./scripts/start-everything.sh status  # read-only overview
```

## What remains to be validated on your Mac

1. The installed Agent Manager 1.0.0 MCP tool-configuration collection path (Go request model `type=mcp`, `envMappings`, and `environmentVariables` is now implemented; runtime path remains to be verified).
2. Runtime API key collection route: script now performs safe GET discovery and attempts creation only if unique. No stable route was established from vendor documentation, so full zero-touch operation may stop there.
3. Source build status JSON from your deployed `amctl` 1.0.0 release, as well as the real invoke URLs for each specialist.
4. A real end-to-end test invoking allowed and denied banking tools through the AgentID/OAuth gateway, not merely the in-app role allowlist.
5. The automatically deployed Portal must reach the real managed Concierge gateway URL from inside Kubernetes. S01–S06 are run via port-forward, and do not prove per-tool OAuth scope denial.
6. If you enable model-backed responses, a configured and attached governed LLM provider.

Do not claim the full banking application works until all six succeed. The script covers the repeatable stages, including **attempting** the six managed agent deployments, role assignments, Portal, and native scenario runner. It cannot claim unconditional success without running against your specific installed Agent Manager backend. The strict final gate also requires an actual gateway-level negative OAuth scope test supplied in `AURORA_MCP_AUTHZ_VERIFY_CMD`; in-app role checks alone are insufficient.

### Official references

- [WSO2 Agent Manager v1.0.0 CLI: agent](https://wso2.com/agent-platform/docs/v1.0.0/reference/cli/agent/)
- [Configure MCP Proxies for an Agent](https://wso2.com/agent-platform/docs/v1.0.0/guides/configure-agent-mcp-proxies/)
- [Authorize Agent Access to MCP Tools](https://wso2.com/agent-platform/docs/v1.0.0/guides/authorize-agent-access-to-mcp-tools/)
- [Secure Agent Endpoints with API Keys](https://wso2.com/agent-platform/docs/next/guides/secure-agent-endpoints-with-api-keys/)

### Security and operational notes

- **No destructive repair**: no `k3d cluster delete`, `colima delete`, PVC wipe, or forced proxy recreation.
- **Temporary tunnel**: Cloudflare Quick Tunnel addresses change on restart. The original MCP publisher is intentionally fail-closed if an existing registered proxy points at a different URL. For truly repeatable restarts use a named stable authenticated tunnel and update the publisher configuration appropriately.
- **Secrets**: generated banking credentials and one-time issued gateway keys are stored in `agent-manager-support/secrets.env`; guard this file and never commit it. `amctl agent create --env-secret` temporarily exposes secret values in process arguments; on a shared workstation use managed secret references instead.
- **No fabricated checks**: `AURORA_MCP_AUTHZ_VERIFY_CMD` must be an actual negative scope test invoking a tool with a token lacking the tool scope and asserting a denial. Do not set it to `true`.
- **No ungrounded "all green"**: script exits nonzero if the platform contract differs, if builds fail, if keys or binding cannot be verified, or if any workflow check fails.

## v2 — repeatable startup and partial-recovery logic

The `up` command now **reconciles** the tunnel, rather than choosing `up` or
`publish` from a single `status | grep` result. It checks all three **owned**
processes (Kubernetes port-forward, authentication bridge, cloudflared), checks
the saved and announced public hostname match, and performs authenticated
local/public probe checks. When all checks pass, no tunnel process is restarted.
When a process is missing, stale, or the public endpoint fails its tests, only
the launcher-owned local processes are terminated and recreated. It refuses to
kill an unrelated process occupying ports 18101 or 18102.

The backing services are not rebuilt on every startup. A SHA-256 fingerprint
of runtime source, Dockerfile, dependencies, and Kubernetes manifests, plus
Kubernetes rollout and secret existence checks, decides whether to reconcile.
An older installation without a fingerprint will be reconciled **once**, then
will be reused on identical launches. To force rebuilding, run:

```bash
AURORA_REFRESH_BACKEND=1 ./scripts/start-everything.sh up
```

When the public Quick Tunnel hostname changes, the all-in-one launcher permits
an *extremely restricted* MCP proxy update: only the same `aurora-banking`
proxy, the `primary` endpoint, the expected environment, the 15 expected tools,
15 one-tool scopes, identity security, the Aurora origin-auth header, and an
old/new `https://*.trycloudflare.com/mcp` pair. It attempts the **PUT update**
and rereads the result; no deletion/recreation or SSRF-rule disabling occurs.
A standalone `publish` remains fail-closed unless
`AURORA_ALLOW_EPHEMERAL_PROXY_UPDATE=true` is explicitly supplied. **The PUT
contract is not yet confirmed against the user's live Agent Manager 1.0.0**;
if it is not accepted, the launcher fails with the backend response and keeps
existing metadata untouched. For stable unattended demos, configure a named
authenticated HTTPS tunnel; Cloudflare Quick Tunnel is temporary and may not
support all streaming MCP transports.

Specialist gateway keys are reloaded from the persisted secret file before
Concierge creation, avoiding empty `AGENT_*_API_KEY` bindings caused by a
stale credential snapshot.

What is **not** possible to assert from offline tests: the installed Agent
Manager's tool-binding API, its one-time gateway key endpoint, the six actual
platform agent build/deploy outcomes, the availability of a Git commit that
matches the intended source, and a real negative OAuth scope test. The
existing final verifier is strict and will exit nonzero if any of those
checks fails. It will never claim everything is deployed on the basis of
mocked tests alone.

To validate the overlay locally:

```bash
bash -n scripts/start-everything.sh
bash -n scripts/publish-mcp-secure-tunnel.sh
python3 -m unittest discover -s agent-manager-support/tests -v
python3 -m unittest discover -s tests -p test_reconciler.py -v
```

Do not commit `agent-manager-support/support.env`, `secrets.env`,
`.mcp-public-tunnel`, or `.backend-fingerprint`.
