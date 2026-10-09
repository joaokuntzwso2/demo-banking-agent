# Banco Aurora — Durable conversational workflow

## Banking story

Camila, a fictional customer, asks Aurora to investigate an unknown credit-card purchase, block her card, open a dispute, make a verified Pix payment, and recalculate her remaining funds. Aurora is the **sole customer-facing agent**. Specialists execute independently and reach banking data and write tools through the upstream Banking MCP service (via WSO2's governed proxy when manually configured on-prem).

## Who calls whom?

```mermaid
sequenceDiagram
  actor Customer
  participant UI as Banking UI
  participant BFF as Portal BFF
  participant Concierge as Aurora Concierge (LangGraph)
  participant Pix as Pix Specialist
  participant MCP as Governed MCP Proxy / Upstream
  participant Core as Banking Core + PostgreSQL
  participant Account as Account Specialist
  participant Wellness as Wellness Specialist
  Customer->>UI: Pay R$1,250 to verified condo Pix; then check balance
  UI->>BFF: POST /api/chat (HttpOnly session cookie + CSRF)
  BFF->>Concierge: POST /chat (gateway auth + internal call capability)
  Concierge->>Pix: POST /chat (specialist call capability)
  Pix->>MCP: request_pix (specialist tool authorization)
  MCP->>Core: Create approval (immutable payload)
  Pix-->>Concierge: Pending approval ID and transaction details
  Concierge-->>BFF: Pending actions + durable workflow ID
  BFF-->>UI: Show beneficiary/amount to customer
  Customer->>UI: Confirm payment
  UI->>BFF: POST /api/approvals (cookie + CSRF)
  BFF->>Concierge: POST /approve (gateway + privileged approval capability)
  Concierge->>Core: Verify approval decision against immutable payload
  Concierge->>Pix: POST /execute (privileged specialist execution)
  Pix->>MCP: execute_approved_pix
  MCP->>Core: Commit payment and idempotent ledger debit
  Core-->>Pix: Payment ID, completed state
  Pix-->>Concierge: Execution receipt
  par Read back AFTER commit
    Concierge->>Account: POST /chat (fresh snapshot)
    Account->>MCP: get_account_balance + list_account_transactions
  and Recalculate AFTER commit
    Concierge->>Wellness: POST /chat (fresh snapshot)
    Wellness->>MCP: get_account_balance + get_upcoming_obligations + policy retrieval
  end
  Concierge-->>BFF: Natural-language receipt + verified amounts
  BFF-->>UI: Receipt, balance and projected funds
  UI->>BFF: GET /api/state
  BFF->>Core: Read-only dashboard snapshot
  BFF-->>UI: Refreshed accounts, payments and ledger
```

## State and recovery

- LangGraph distinguishes interpretation, planning, independent specialist reads, and response composition. The Concierge persists a workflow record in the Core so the browser can recover its workflow ID and remaining approvals after reconnection.
- Each sensitive operation requires a specific immutable approval payload. A duplicate approval decision is recognized. Payment execution uses the approval ID as its stable idempotency key, preserving **exactly one debit** even if a network response disappears. Dispute and card blocking also support replay-safe operation results.
- Mutations are executed by Card, Dispute or Pix specialists; the BFF cannot bypass Concierge to call those specialists. The initial and resumed conversations are linked by workflow ID.
- A successful write is not reported as reversed simply because a later financial read-back fails. In that event Aurora reports completion **without inventing the updated balance**.

## Safety and language

- A question about Pix, a negative instruction ("don't pay") or an ambiguous amount cannot silently become an approval. Card, dispute and beneficiary entities must resolve uniquely, or Aurora asks a clarifying question.
- English and Spanish are supported in the portal and user-facing agents; transaction amounts originate from bank records. Human-readable policy citations carry version/date metadata.
- The local portal issues an expiring signed HttpOnly session cookie after a **demo-only** access code, validates CSRF on mutations, and protects presenter reset separately. On shared environments use HTTPS, `DEMO_SECURE_COOKIE=true`, secret injection and strong access controls. This is not customer OIDC/MFA.
- Execution activity in the portal is **application-level workflow metadata**. To see actual distributed traces and MCP authorization, inspect WSO2 Agent Manager instrumentation after manual deployment.

## Scenarios to verify

1. Reset. Ask about expenses. Starting R$8,450 minus R$2,100 obligations = projected R$6,350.
2. Ask to pay R$1,250 to the verified condominium, then check balance; approve. Expected R$7,200 balance, R$5,100 remaining after commitments.
3. Repeat the same approval decision, including after a simulated lost response. No second Pix transfer.
4. Reset. Ask to block the card ending 4421 and dispute the R$1,280 Orion Digital transaction. Both actions require independent confirmation; a dispute does **not** credit checking automatically.
5. Ask "Do not pay R$1,250 by Pix" and use a multi-amount request. No payment approval is created.
6. Switch to Spanish and ask the same story. Confirm Spanish prompts and confirmation, with authoritative record IDs and amounts.
