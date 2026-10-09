# Banco Aurora — Synthetic Banking Domain Contract

## Truth and identity

Banking Core (`mock_core`) is the **authoritative source** of customer financial state. Agent-generated text, policy retrieval and Activity UI are not substitutes for ledger records. The browser's signed demo session resolves only to fictional customer `C001`; customer `C002` exists only for ownership tests.

## Account and card

Camila's checking account starts at R$8,450. An unrelated Orion Digital credit-card transaction of R$1,280 does **not** automatically debit this checking account. Opening a claim records `under_review`; it does not grant a refund or change checking balance. A card block sets `blocked`, preventing the synthetic card from appearing active; it does not undo historical purchases.

## Beneficiaries and Pix

- `BEN001` Condomínio Jardim do Sol: **verified**, authorized candidate if the customer names it explicitly.
- `BEN003` Urgent Settlement Services: **unverified**, denial fixture. Do not authorize a payment.
- No payment succeeds without an exact beneficiary ID, positive BRL amount (up to the fictional R$2,500 limit), sufficient checking balance and an immutable customer approval.
- On approved Pix: lock approval/account, create exactly one `Payment`, debit Account once, create one `Ledger` record and one `pix_completed` audit event, then mark approval consumed. Idempotency key must equal `PIX-<approval-id>`.
- A retry with the same approval must return the same outcome; a new different approved transfer is a new operation.

## Financial planning

Total known obligations: electricity R$340, groceries R$950, internet/mobile R$210, insurance R$600 = **R$2,100**. After R$1,250 Pix: checking R$7,200, projected R$5,100. A hypothetical payment subtracts money only from a simulation, and does not create approvals, payments or ledger records. Forecast is educational and excludes unrecorded spending or future income.

## Human service case

Customer must explicitly ask for a human. Dispute Agent may create a case through `request_human_escalation`; there is no financial mutation or guaranteed outcome. Core persists `waiting_for_agent` → `assigned` → `in_progress` → `resolved`. Only the separate presenter operator credential may advance states. Requests to assign a case belonging to another customer are rejected. Repeating the same open request is deduplicated.

## Risk and prompt injection

Risk Advisor is optional, independent and **read-only**; it reports recipient verification state, not a fraud probability. Pix enforces verification even if Risk is unavailable or gives bad advice. A request to display another customer's records is refused by Concierge before specialist selection. Core authorizes ownership on protected endpoints even if a model-generated prompt is malformed.

## Human approval is not bank authentication

The synthetic access-code login/approval confirmation are UI controls for this fictional demonstration. They are **not** real bank customer OIDC, regulatory step-up or money movement, and must never be presented as such.
