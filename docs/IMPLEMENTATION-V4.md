# Executive v4 — Implementation and Manual Agent Manager Readiness

## Scope and verified behavior

This is a **source-level upgrade**, not an Agent Manager install or a provisioning tool. Banco Aurora stays a synthetic bank (no real Pix, cards, regulated customer identities or bank authentication). The six core agent deployments remain separate applications; the optional seventh Risk Advisor remains read-only/external.

### Implemented in code

| Area | V4 improvement | Verification |
|---|---|---|
| Customer-facing orchestration | Typed planning task metadata, explicit span around orchestration; existing safe approval resume/verification unchanged | Unit, local scenario and real OpenTelemetry checks |
| Governed inference | Dispute/Wellness bounded LLM explanation via `LLM_PROVIDER_URL`/`LLM_PROVIDER_KEY`, deterministic authoritative business facts, safe fallback on invalid *content* | New unit regression tests + real provider dry run |
| Gateway guardrail errors | Governed model denials propagate without a direct-LLM fallback | Injected failure regression, native policy test **manual** |
| RAG policy lifecycle | Effective-date selection, expired/future exclusion, one active version per logical policy and language | Past/present/future fixture tests |
| Semantic retrieval | Explicit managed embedding URL/key contract; no upstream-provider bypass | Managed-mode unit test; compatibility **manual** |
| Privacy | Account specialist drops full beneficiary directory; own Pix receipts include display name only | Source check and receipt regression |
| Distributed context | Named OTel child spans + W3C context headers; safe attributes | Unit test and live collector **manual** |
| UI | RAG citations appear as specialist evidence, not invented WSO2 spans | JS syntax + browser walkthrough |
| Kubernetes | Case/operator secrets passed to Core and Portal | Manifest regression |

### Provider behavior

For Concierge use `USE_LLM_PROVIDER=true`, `REQUIRE_GOVERNED_LLM=true`; set `LLM_PROVIDER_URL`, `LLM_PROVIDER_KEY`, and `OPENAI_MODEL` via your managed deployment bindings. For Dispute and Wellness, additionally set `LLM_GROUNDED_ANSWERS=true` to activate model-generated **explanatory** text. The managed URL is expected to implement the OpenAI chat-completions contract as consumed by `AsyncOpenAI`; confirm the URL/auth/model format against your installed Agent Manager gateway. No automatic fallback to an unmanaged provider occurs.

The model is not trusted to decide a Pix amount, transaction ID, whether a beneficiary is verified, card/dispute eligibility, customer ownership, approval result, or account balance. All these are read from MCP/Core or verified in Core. The LLM's optional single-sentence addendum is filtered for numbers and obviously unsafe claims; this is a defense-in-depth *format gate*, not a semantic truth guarantee. A real managed-model content guardrail is configured and demonstrated **manually**.

### Policy chronology

`POL-DIS-002` has a synthetic expired v0.8 fixture, active v1.0, and future-dated v2.0. `active_versions` excludes expired/future records before the lexical/semantic ranking stage. `POLICY_AS_OF_DATE=2026-10-09` is an optional deterministic demo pin, not a global production policy date. Date format is ISO `YYYY-MM-DD`. For live demonstrations, leave it unset unless explicitly showing temporal-version behavior. Rebuild the FTS5 index after changing policies; the index refresh is SHA256-based.

### WSO2 Agent Manager acceptance, **manual only**

1. Deploy each agent from the six Git project paths; verify `/health` version and actual managed invoke URL.
2. Register the separate upstream MCP; grant one scope for each of the fifteen tools. Verify Account balance succeeds, Account Pix-write fails with a gateway-enforced denial. Local allowlist failures do not count.
3. Bind the LLM provider to Concierge, Dispute, Wellness; verify all three make real managed model requests and capture usage and gateway results.
4. Record a real traced request and approval continuation in WSO2. Cross-request approval may create two trace roots; link them through the workflow/correlation ID while avoiding customer-data attributes.
5. Run native evaluations against actual traces. Application-local scoring is distinct and never represented as native WSO2 quality scores.
6. If demonstrating releases, create versioned deployments/promotions/rollback in the WSO2 console, not from the banking frontend.

### Suggested fail-safe live-demo procedure

Keep `LLM_GROUNDED_ANSWERS=false` until the managed model binding has been tested; in that mode Dispute and Wellness remain deterministic and the business path still works. Configure a private collector and turn on `AURORA_ENABLE_OTEL=true` only after confirming the runtime isn't already auto-instrumented by Agent Manager. Capture *actual* trace evidence and verify its spans. Do not enable both independent SDK providers for the same workload.

### Exact secrets and networking notes

- Portal BFF has Concierge invoke credentials only; never expose specialist URLs/keys to JavaScript.
- Specialists get the governed **MCP Proxy** URL, not the private upstream MCP service address.
- The private upstream MCP server must be reachable by WSO2's gateway, not necessarily by the browser.
- The Core API, its approval endpoints and its operator endpoints must not be publicly exposed.
- Internal shared credentials and the demo access code are demo-grade. Production IAM/step-up, mTLS and proper delegated end-user authorization are separate projects.
- Don't publish `.env` files with real secrets; only track the `.env.example` templates.

### What is not implemented

The upgrade does not create Agent Manager orgs/projects, MCP proxies, OAuth roles, managed LLM provider resources, guardrails, native evaluation monitors, Agent Kinds, deployment promotion, rollback or observer/admin MCP configuration. Those tasks are intentionally left manual and are documented in `docs/PLATFORM-SCENARIOS.md` and `docs/DEPLOY-MANUAL-AMP.md`.
