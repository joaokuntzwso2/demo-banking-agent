# Banco Aurora — Agent Manager 1.0.0 verified LLM binding fix

This file documents the **integrated reconciler patch**, replacing the obsolete v2 audit-only README. No Agent Manager cluster, database, MCP Proxy, credentials, or keys are altered by installation. The patch modifies the local reconciler and diagnostic scripts, and adds tests.

## Confirmed live contract (October 10, 2026)

- Collection `GET /orgs/default/projects/banco-aurora/agents/{agent}/model-configs` returns `configs[]` summaries with each **configuration's own UUID**. A provider's UUID is not a config UUID.
- Individual `GET /orgs/default/projects/banco-aurora/agents/{agent}/model-configs/{config-uuid}` returns **`envMappings`**, not `envModelConfig`, on the installed runtime (amctl 1.0.0 commit `21b1adb2e`).
- The runtime mapping is `envMappings.default.configuration.providerName = aurora-openai`; `providerUuid = 6aaa1fb6-58dd-402d-82bd-ff4b828e1a24`, plus a **distinct** `proxyUuid` per agent, status `active`, and the correct `url/apikey` environment variables.
- Alternative DTOs use `envModelConfig.default.llmProxy`. The parser supports both, but fails closed when incompatible/conflicting shapes coexist.
- `aurora-account` config UUID `43752396-6f76-4f3b-89f0-550b9b1fac6c` and `aurora-card` config UUID `d8e10bfe-f9c9-4e48-9e24-d6602c9f623e` are both valid and reference the same org provider. Neither should be replaced.

## What changed

- `scripts/aurora-full-reconcile.py`: read and validate the actual detail mapping; check org provider **handle/UUID** rather than binding name; validate proxy association/status, environment and runtime variable keys. Healthy account/card mappings cause **zero model-config writes**. Only a brand-new binding causes `POST` (with read-back). Verified existing bindings with *exactly* `url`/`apikey` keys but incorrect injection variable names can be repaired through a narrow `PUT` of `environmentVariables` only, followed by a fresh full read-back. Unmapped, missing, stale, cross-provider, or ambiguous existing configurations fail closed without provider writes. Provider and model-config collection pagination must be complete.
- Build reuse: match an existing successful managed build to the desired source commit, either from an explicit remote SHA or matching private local state plus successful remote status. If remote build is completed but its commit is unprovable and no matching private record survives, **stop rather than request another build**. Do not confuse a previously completed build with the new asynchronous build request.
- `scripts/aurora_llm_contract.py` and `scripts/diagnose-aurora-llm.py`: retain read-only audit behavior, now recognizing the live `envMappings` family.
- Tests contain actual sanitized account/card list/detail JSON, conflicting provider UUID/name tests, missing/ambiguous mappings, safe variable-name repair/read-back, and build idempotence checks.

Unchanged: `scripts/start-everything.sh`, `scripts/sync-managed-agent-source.py`, existing `tests/test_reconciler.py`, all external resources, source agents, secrets and backend manifests.

## Install safely

From the repository root on your Mac:

```bash
cd /Users/joaoluiskuntz/wso2mi/Projects/Demo/demo-banking-agent

# Optional local backup of the existing working scripts (no secrets in package):
cp scripts/aurora-full-reconcile.py ~/Downloads/aurora-full-reconcile.before-llm-fix.py
cp scripts/aurora_llm_contract.py ~/Downloads/aurora_llm_contract.before-llm-fix.py 2>/dev/null || true

# ZIP file should be placed under ~/Downloads on the Mac.
unzip -l ~/Downloads/banco-aurora-amp100-llm-fix.zip
unzip -o ~/Downloads/banco-aurora-amp100-llm-fix.zip -d .

python3 -m py_compile scripts/aurora-full-reconcile.py scripts/aurora_llm_contract.py scripts/diagnose-aurora-llm.py
python3 -m unittest discover -s tests -p 'test_*.py' -v
python3 scripts/diagnose-aurora-llm.py --agents aurora-account aurora-card

git diff --check
# Verify the patch only touched expected local files; nothing committed or pushed.
git status --short
```

The read-only diagnostic should print `VERIFIED` for account/card. This proves the model-binding details only; it does **not** exercise the LLM provider's external token, the agent runtime, or MCP authorization.

## Resume existing deployment

```bash
unset AURORA_AGENT_SOURCE_PUBLISH
./scripts/start-everything.sh agents
```

This preserves stages already completed. After live deployment and all stricter checks succeed, the intended normal launcher is:

```bash
./scripts/start-everything.sh up
```

**Do not claim six-agent success from these local tests.** This environment cannot run commands against your Mac/k3d cluster. The original orchestrator still contains separate gates: MCP tool configuration endpoint discovery, gateway API key issuance, AgentID role assignments, and S01-S06/authorization verification. If a new stage fails, inspect the exact GET/CLI contract rather than replacing healthy resources.

## Read-only raw checks

```bash
amctl llm-provider list --org default --json
amctl api -X GET /orgs/default/projects/banco-aurora/agents/aurora-account/model-configs
amctl api -X GET /orgs/default/projects/banco-aurora/agents/aurora-account/model-configs/43752396-6f76-4f3b-89f0-550b9b1fac6c
amctl api -X GET /orgs/default/projects/banco-aurora/agents/aurora-card/model-configs/d8e10bfe-f9c9-4e48-9e24-d6602c9f623e
amctl agent build list aurora-account --project banco-aurora --org default --json
```

Do not post raw gateway secrets, API keys or OAuth client secrets to logs, Git, or chat.

## References

- Official v1.0.0 CLI: https://wso2.github.io/agent-manager/docs/v1.0.0/reference/cli/agent/
- Official v1.0.0 LLM configuration guide: https://wso2.com/agent-platform/docs/v1.0.0/guides/configure-agent-llm-configuration/
- Release source DTOs: https://raw.githubusercontent.com/wso2/agent-manager/amp/v1.0.0/agent-manager-service/models/agent_configuration_dto.go
- The decisive evidence for the installed runtime is the real, credential-free `amctl api GET` capture in `tests/fixtures/amp_100_llm_live.json`.
