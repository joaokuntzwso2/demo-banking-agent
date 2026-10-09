# Executive Readiness Hardening — Changes

Relative to the previous `demo-banking-agent-git-ready` source snapshot:

- Removed a public, hard-coded `X-Demo-Session` token from browser JS. BFF now issues signed, expiring HttpOnly cookies after a demo access code; mutation endpoints require a CSRF token. Presenter reset requires a separate code.
- Split single broad internal key into read, write, workflow/approval, reset, Concierge chat, specialist chat and specialist execute capabilities. Old fallback only in local mode; managed mode fails closed on missing secrets.
- Added role-specific inbound authentication for specialist and Concierge chat endpoints. No browser direct specialist calls.
- Core `/approvals/.../decide` is replay-safe; Pix approval locks, stable idempotency, dispute and card-block replay allow reconciliation after lost responses.
- Added stricter financial intent parsing and unique card/transaction/beneficiary resolution. Ambiguous multi-amount payments ask for clarification.
- Extended LangGraph and persisted workflow sessions for customer approval recovery and turn clarification. Sensitive writes remain gated behind approval and executed by specialists; read-back is post-commit.
- Added a second fictional customer, reset-relative obligations, and fresh future-date filtering.
- Expanded fictional bilingual policy corpus to 40 versioned records and persistent retrieval index with optional offline-ingested embedding vectors.
- Added structured audit correlation, optional OTLP instrumentation, execution steps separated from business audits, improved banking views and payment receipts.
- Added executable write-capable BFF integration test and GitHub Actions quality workflow. Unit tests must pass; actual Docker end-to-end and real Agent Manager deployment are not run by this package build.

Known residual production gaps: true OIDC identity, MFA/step-up, distributed workflow/state machine orchestration with explicit outbox reconciliation, WSO2 on-prem deployment verification, actual gateway OAuth policy installation, browser UI cross-browser testing and secure secret management/rotation. Those require the target infrastructure and are intentionally not represented as completed tasks.
