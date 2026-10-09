# Banco Aurora — VP Live Demo (10 minutes)

## Prerequisites

- Clean private synthetic deployment; Core healthy; six agents healthy; upstream MCP working. Real Agent Manager deployment and MCP proxy are **manual**.
- Presenter signs in using `DEMO_ACCESS_CODE` and holds a separate `DEMO_RESET_CODE`.
- Reset the simulator before presenting; verify Camila C001, R$ 8,450 balance and R$ 2,100 upcoming commitments.
- When showing Agent Manager, ensure six actual managed builds and real MCP scope policies are configured. Don't show a local simulator as if it were managed governance.

## Narrative

| Time | Customer experience | Platform evidence |
|---|---|---|
| 0:00–1:00 | Camila sees checking and credit-card activity | Real state loaded from Core |
| 1:00–3:00 | `I don't recognize the R$1,280 Orion Digital charge. Block my card and open a dispute.` | Concierge routes Card + Dispute; retrieval citation; two separate approvals |
| 3:00–4:00 | Authorize both and inspect Cards + Disputes | Card blocked, case under review, no automatic checking debit or refund |
| 4:00–6:00 | `Pay R$1,250 to my verified condominium Pix beneficiary, then check my remaining balance.` | Pix approval; specialist execution; single ledger debit |
| 6:00–7:00 | Checking R$7,200; projected after future bills R$5,100 | Fresh read-back and payment receipt ID |
| 7:00–9:00 | Switch to Agent Manager manually | Six managed agents, MCP proxy tool permissions, real traced execution, unauthorized-tool denial |
| 9:00–10:00 | `¿Cuánto dinero me queda después de mis próximos gastos?` | Correct Spanish customer response; source-backed calculation |

## Prepared questions and objections

- **Does the LLM make payments?** No. It selects specialists and proposes approvals. The backend checks immutable approved details, ownership, beneficiary verification, limits, funds and idempotency before committing.
- **Can the Account agent call a Pix tool?** The local role allowlist blocks it. In actual Agent Manager, demonstrate a *separate* gateway OAuth denial based on explicit MCP tool scopes.
- **Is this production?** No; it is fictional banking data with hardened demo boundaries. Real OIDC, step-up MFA, operational reconciliation and regulated controls are not implemented.
- **Is this RAG?** Two agents retrieve versioned documents from a persistent lexical index by default; optional persisted embedding vectors provide semantic retrieval when explicitly populated. State the exact mode used.
- **What happens if HTTP fails after payment commit?** Retry the same approval ID. The Core returns its committed payment and never debits twice.

## Negative live tests (run beforehand, optional on stage)

- `How does Pix work?` must not create payment approval.
- `Do not pay R$1,250 to my condominium.` must not create payment approval.
- `Pay R$1,250 to my verified beneficiary.` must ask whom to pay, not assume BEN001.
- `Dispute the charge.` must ask which transaction, not hardcode TXN-ORION.
- Press authorization twice only through the same approval and confirm same receipt/payment count.

## Recovery

If the browser loses the result after approval, refresh; pending approval controls are reconstructed from Core workflow state. Retry **the same** approval. Do not reset the DB midway through a payment without explaining it is a fictional demo. If OTel fails, continue the business story and explicitly disclose that live traces are unavailable instead of substituting the local audit stream.
