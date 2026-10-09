# Banco Aurora — Critical Fixes Delivered

**Release scope:** application-level security and reliability, financial workflow integrity, real agent orchestration, better RAG, a more comprehensive UI, reproducible checks and executive demo storytelling.

**Deliberate exclusion:** configuring or actually deploying to a specific on-premises WSO2 Agent Manager cluster is **manual**, not an application software defect. The six independent Git build contexts and per-agent environment templates remain supplied; there are no automatic cluster operations.

## What changed, and why

| Priority | Defect / concern | Implemented mitigation | Validation |
|---|---|---|---|
| P0 | Shared, hardcoded browser session / single backend credential | Signed expiring HttpOnly cookie issued after a demo access code; CSRF guard; separate presenter-reset code; separated read/write/approval/reset and inter-agent credentials; fail-closed secrets in managed mode | Unit tests; Docker integration script pending target runtime |
| P0 | Pix double-debit or lost approval acknowledgement | Approval ID-derived Pix idempotency key; DB row locking; replay-safe approve/execute; card/dispute replay-safe; presenter sees persisted readback | Core regression tests and write-capable integration test script |
| P0 | Unintended financial action from loosely interpreted text | Explicit positive command, negation/informational checks, unique resource identification, exact BRL parsing, and approval payload match | Intent tests, Core tests |
| P1 | Thin customer-facing routing without persisted state | LangGraph understand/plan/invoke/compose stages, parallel independent specialist reads, persisted workflow ID and clarification state; only Concierge resumes approvals; verify after commit | Contract tests, pending workflow integration checks |
| P1 | Four tiny policy snippets / falsely implied vector RAG | Forty fictional versioned bilingual policy records; persistent SQLite FTS5 index and grounded citations, optional explicitly ingested embeddings with retrieval mode reported | Policy index tests; embedding mode requires model credentials |
| P1 | Activity panel confused business audit with Agent Manager traces | Correlation IDs throughout the app, optional OTLP instrumentation, workflow-step UI explicitly labeled not OTel, and separate Core business audit events | Instrumentation source checks; actual platform spans must be confirmed on-site |
| P1 | Generic frontend transaction lists | Approval cards, Pix receipt, card/dispute detail, financial projection and English/Spanish text; selected customer state refreshed after sensitive writes | Browser test provided as opt-in (requires installed Chromium and running stack) |
| P0 | Old `.env` set agent URLs to `localhost`, breaking calls inside containers | Local Compose now uses Docker service DNS unconditionally; remote Agent Manager URLs stay in separate hosted-agent/hybrid templates | Compose config checked; full runtime pending |
| P1 | No reproducible write-path test and quality gate | `scripts/integration-test.py`, basic smoke checks, opt-in browser test, GitHub Actions static+unit job and manually triggered Compose integration | 28 passed / 3 skipped in package-builder environment |

## Security boundaries

```
Browser (no service secrets)
  -> Portal BFF (demo session + CSRF, fixed synthetic C001 identity)
  -> Concierge (gateway API key + private service capability)
  -> Five independent specialist agent endpoints
  -> WSO2 governed MCP Proxy [manual gateway scopes and AgentID role binding]
  -> upstream Banking MCP service
  -> Banking Core (separate write and read capabilities)
  -> PostgreSQL ledger / approvals / workflow records
```

The portal also reads its dashboard projection directly from private Core using the **read-only** capability. The browser never invokes a specialist or the MCP service directly. The Concierge does not have any direct banking MCP Proxy tool permission.

## Run after applying the update

```bash
cp .env.example .env  # only if this is a new checkout; preserve an existing .env
# Existing .env values: add/replace the NEW BANK_CORE_READ_KEY,
# BANK_CORE_WRITE_KEY, BANK_APPROVAL_KEY, BANK_RESET_KEY,
# CONCIERGE_CALL_KEY, CONCIERGE_APPROVAL_KEY,
# SPECIALIST_CALL_KEY, SPECIALIST_EXECUTION_KEY,
# DEMO_ACCESS_CODE, DEMO_RESET_CODE, DEMO_SESSION_SIGNING_KEY.
docker compose up -d --build --force-recreate
bash scripts/smoke-test.sh
python scripts/integration-test.py # WARNING: writes and resets synthetic state
open http://localhost:8300
```

If migrating from an old seeded database, invoke **Reset demo** using the presenter reset code to replace the old fictional dataset with the new version; the startup path will not destroy existing data. **Do not use `docker compose down -v`** to apply this release.

The UI sign-in code in local defaults is `aurora-local-demo-only` and presenter reset code is `local-presenter-reset`; these are known examples **only for isolated local laptops** and must never be exposed on a shared installation. Set `DEMO_SECURE_COOKIE=true` behind a genuine HTTPS origin.

### Optional browser check

```bash
python -m pip install playwright
python -m playwright install chromium
AURORA_RUN_BROWSER_TESTS=1 pytest -q tests/e2e/
```

### Tests that must pass before a VP presentation

1. The portal requires login; requests without CSRF cannot modify demo state.
2. The customer's initial account is R$8,450 and the distinct card charge is R$1,280.
3. A negated Pix request or a question about Pix creates no approval.
4. Card blocking and dispute creation create **two** independent approval actions.
5. Pix approval of R$1,250 creates **one** payment, **one** debit, and R$7,200 account balance.
6. Repeated same-approval execution has the same payment ID and no second debit.
7. Financial Wellness recomputes remaining funds at R$5,100 after future commitments of R$2,100.
8. Spanish response matches the persisted amounts; no JSON response is shown in the chatbot.
9. A deliberate wrong entity/customer/payload is rejected at the Core.
10. **When manually configured on Agent Manager**: a read-only agent's write-tool request is denied by the real WSO2 MCP Proxy; the actual distributed trace is visible in the installed telemetry UI.

## Known boundaries

- **Not a production banking authorization system.** Real banking deployment requires a genuine customer identity provider, scoped user delegation, MFA/step-up for payment, transaction risk scoring, secrets rotation, secure transport, compliance review and regulated financial integrations.
- The persistent SQLite FTS5 corpus qualifies as indexed retrieval; **vector embeddings are optional**, not active unless ingested and configured. No pgvector backend is represented as present.
- The workflow persists application state and supports replay safety, but does **not** implement an enterprise durable queue, saga engine or transactional outbox for external non-idempotent systems.
- Optional OTel library instrumentation is present. **Actual end-to-end traces, policy denial and AgentID tool permissions can only be verified during manual deployment to your installation.**
- Full Docker + browser + WSO2 runtime integration has not been executed in this package-building environment. The packaged scripts establish those tests on your local stack.
