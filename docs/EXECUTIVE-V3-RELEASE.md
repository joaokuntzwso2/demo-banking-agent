# Banco Aurora Executive v3 — upgrade and acceptance checklist

## Source baseline and limitations

This complete-source upgrade is based on the latest VP-hardening snapshot prepared in the current conversation and the user's reported `b2f746c` Git commit. The public GitHub remote could not be fetched in the preparation environment. **Review the diff against your local main before committing.** This package does not deploy to WSO2 Agent Manager.

## New application capabilities

- Eight fully described scenarios, with S01–S06 executable against the simulator and S07–S08 performed *manually* in the native Agent Manager console.
- Optional seventh independent, externally hosted, **advisory** beneficiary Risk Agent.
- 15 purpose-specific MCP tools instead of the unrestricted banking snapshot tool, and a scope/AgentID governance matrix.
- A synthetic unverified recipient and deterministic refusal; a read-only what-if projection without side effects.
- Human service-case persistence and separate operator-only workflow transitions.
- A bilingual Executive Scenario Studio in the banking portal.
- Managed LLM provider environment binding with no silent direct-provider fallback in managed mode; provider compatibility must be verified on the installed gateway.
- 60 fictional versioned knowledge records with persistent FTS5 and optional semantic embeddings.
- Read-only smoke/unit and opt-in write-capable scenario test scripts, a deterministic *local* scorecard, an environment preflight, a full manual platform playbook, and regenerated independently buildable Git agent projects.

## Apply to existing Mac checkout

1. `git status` — save any existing work or make a backup. The archive contains application files and example envs, but no actual `.env` and no `.git`.
2. From the parent of your repository, unzip `demo-banking-agent-executive-v3.zip`, preserving your existing `.env` and Git directory.
3. `python3 deployment/agent-manager/scripts/prepare-apps.py` then inspect `git diff --stat`.
4. Run `docker compose up -d --build --force-recreate` using your existing `.env`. Existing database state is retained. **Reset the synthetic database from the presenter UI** to seed BEN003 and use the expanded scenario fixtures.
5. `bash scripts/verify-executive.sh` runs smoke + isolated Core unit tests (read-only for the running stack).
6. `bash scripts/verify-executive.sh --full` runs S01–S06 against the real local portal, performs writes, resets the synthetic database before and after, and writes a gitignored local report.
7. Open `http://localhost:8300`, log in using your configured `DEMO_ACCESS_CODE`, and select the eight scenarios from the new studio.
8. Apply endpoint and MCP URL values to *private runtime* environments following `docs/DEPLOY-MANUAL-AMP.md` and `deployment/agent-manager/.env.*.example`.

## Correct evidentiary distinctions

The simulator and local test runner can prove only **application behavior**. They do **not** establish managed deployments, native AgentID gateway scopes, native trace monitors, provider guardrails, rate limits, Agent Kinds or promotion/rollback. Those must be configured and independently verified in the on-premises Agent Manager instance. The optional seventh external agent is registered there manually as an external agent and remains advisory.

## Expected synthetic banking figures

- Initial checking balance R$8,450.
- Card transaction R$1,280, unrelated to checking.
- Approved Pix R$1,250 to verified BEN001 results in R$7,200 checking.
- R$2,100 known upcoming obligations, leaving R$5,100 after Pix.
- A further **hypothetical** R$1,250 payment after the Pix yields R$3,850, with **no additional payment**.
- BEN003 (Urgent Settlement Services) is unverified and cannot be authorized.
- Repeat Pix approval returns the same persisted payment and never debits twice.

## Local quality checks performed during package build

- Pytest source regression suite: 35 passed, 3 skipped (optional packages not installed in build environment).
- Python source compilation, JavaScript syntax check, shell syntax check and YAML parsing.
- Generator rerun for six platform-hosted apps, standalone MCP upstream and optional external Risk Agent.
- Archive integrity and path/security exclusions checked.

**Not performed:** Docker/PostgreSQL full integration or Agent Manager managed runtime in the build environment. Run the included scripts and the real WSO2 manual acceptance checklist before presenting.
