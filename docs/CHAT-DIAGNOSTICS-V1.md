# Banco Aurora — isolate first S01 chat failure

This is an ADDITIVE diagnostic, **not** a v9.5 reconciler replacement.
It does not change Agent Manager, pod deployments, Kubernetes resources, credentials, Git, database balances, or approve financial operations.
A chat request may persist conversational workflow state in the synthetic Banking Core.

## Installation

From the existing `demo-banking-agent` repository root:

```bash
unzip -o ~/Downloads/banco-aurora-chat-diagnostics-v1.zip -d .
python3 -m py_compile scripts/diagnose-aurora-chat.py
python3 -m unittest discover -s tests -p 'test_chat_diagnostic_v1.py' -v
```

## Run

```bash
python3 scripts/diagnose-aurora-chat.py
```

It takes ownership of its own temporary loopback-only port-forward to
`svc/aurora-portal` remote port 8300, never using/killing an existing listener.
Uses private `agent-manager-support/secrets.env` for `DEMO_ACCESS_CODE`,
without printing the code. Tests login, authenticated `GET /api/state`,
then non-transactional `POST /api/chat` with a balance question. If that fails,
it runs a direct authenticated Concierge POST from inside the existing Portal
pod. It uses credentials already present as environment variables in that pod,
not credential values in `kubectl` arguments. It prints HTTP statuses, sanitized
FastAPI error categories, and action names only. The script cleans up its own
port-forward even on failure.

The direct check is to isolate a BFF/client/gateway issue from a Concierge
application/backend/LLM problem; it is not a native authorization scope test.

If the read-only balance chat passes but the scenario S01 does not, use:

```bash
python3 scripts/diagnose-aurora-chat.py --s01
```

That optional switch reproduces the first scenario's prompt. It **may create
pending approvals** in the synthetic workflow but it does NOT approve any
operation and does NOT reset the simulator. The approval flow is not safe to
retry blindly against real customer systems. This is only for the fictional
Banco Aurora demo.

**Do not paste** `agent-manager-support/secrets.env`, raw pod environment dumps,
Kubernetes Secret resources, or HTTP Authorization/X-API-Key header values.

### Diagnostic interpretation

- Portal login != 200: wrong access code/session/cookie or Portal login issue.
- Portal banking state GET != 200: Portal to Core connection/capability issue.
- Portal chat 4xx: use sanitized detail to separate auth, routing, validation.
- Portal chat 5xx, direct Concierge chat 200: investigate Portal-to-Gateway
  request configuration, proxy handling, headers, and timing.
- Portal chat 5xx, direct Concierge chat also 5xx: investigate Concierge logs,
  governing LLM route and Banking Core connectivity. The Kubernetes Ready probe
  does not guarantee handler health.
- Both return 200 but S01 missing `block_card` / `open_dispute`: investigate
  Concierge specialist routing and specialist response errors/permissions.

The existing Portal verifier only checks authenticated `/openapi.json` over
the gateway. The full S01-S06 suite still needs to pass and reset state; this
diagnostic by itself does NOT certify a successful banking scenario.
