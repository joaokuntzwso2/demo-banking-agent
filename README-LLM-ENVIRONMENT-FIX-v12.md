# Banco Aurora — Managed LLM Environment Reconciliation v12

## Problem

Stage 5 fails on an already existing agent:

```text
ERROR: aurora-account: LLM configuration lacks envModelConfig['default']; cannot verify its actual provider
```

The earlier verifier assumed `envModelConfig` was keyed by the literal environment name `default`. WSO2 Agent Manager v1.0.0 returns an **environment-indexed map**, which may be keyed differently; each entry may carry `environmentName`. A CLI-created `openai` config can also exist before the default environment is attached.

## What changes

Only `scripts/aurora-full-reconcile.py` and tests are included. No Git source, manifests, agent bundles, API keys, GitHub Actions, tunnel state, databases, or other resources are changed by extraction.

- Accept an LLM environment entry identified by key `default`, its known environment UUID from `agent-manager-support/support.env`, or the entry's own `environmentName: default`.
- Verify `llmProxy.providerName == aurora-openai` from the specific environment entry. Do **not** equate `openai` config name with `aurora-openai` provider handle.
- If a **unique** LLM config is entirely unmapped and has a recognized WSO2-generated OpenAI template name (`openai`, `openai-2` etc.), attach `aurora-openai` to the default environment with the official `envMappings` update shape, and verify it with an additional `GET`.
- Repair `LLM_PROVIDER_URL` / `LLM_PROVIDER_KEY` names only after verifying the actual provider, and re-read to ensure persistence.
- Refuse replacement of another provider, ambiguous/multiple configurations, unrecognized custom config names, or an environment mapping whose identity cannot be confirmed.

## Apply locally

```bash
cd /Users/joaoluiskuntz/wso2mi/Projects/Demo/demo-banking-agent
unzip -o ~/Downloads/Banco-Aurora-LLM-Environment-Fix-v12.zip
python3 -m py_compile scripts/aurora-full-reconcile.py
python3 -m unittest discover -s tests -p 'test_llm_environment_v12.py' -v
unset AURORA_AGENT_SOURCE_PUBLISH
./scripts/start-everything.sh agents
```

The `agents` subcommand reuses the existing Agent Manager, published agent source, and MCP Proxy. It may reconcile agent-specific model bindings and agent deployments. The patch **does not** authorize a Git push.

## Safe read-only diagnostic (if still blocked)

First inspect config names and UUIDs:

```bash
amctl api /orgs/default/projects/banco-aurora/agents/aurora-account/model-configs \
 | python3 -c 'import sys,json; x=json.load(sys.stdin); x=x.get("data",x); rows=x.get("configs",[]) if isinstance(x,dict) else []; print(json.dumps([{"uuid":r.get("uuid"),"name":r.get("name"),"type":r.get("type")} for r in rows],indent=2))'
```

For a UUID from that list, replace `CONFIG_UUID` below:

```bash
amctl api /orgs/default/projects/banco-aurora/agents/aurora-account/model-configs/CONFIG_UUID \
 | python3 -c 'import sys,json; x=json.load(sys.stdin); x=x.get("data",x); env=x.get("envModelConfig") or {}; print(json.dumps({"name":x.get("name"),"type":x.get("type"),"environments": {k: {"environmentName": v.get("environmentName"),"providerName": (v.get("llmProxy") or {}).get("providerName")} for k,v in env.items() if isinstance(v,dict)}, "injectionVariableNames":[v.get("name") for v in (x.get("environmentVariables") or []) if isinstance(v,dict)]}, indent=2))'
```

The above deliberately suppresses tokens, model gateway API keys, and any secret values. Review output for other sensitive or identifying metadata before sharing.

## Validation performed

- 21 LLM binding regression tests (original v11 + new v12)
- 18 managed-agent reconciler regressions
- 8 OpenAI-provider regressions
- 9 MCP tool-binding regressions
- 3 build-state regressions
- Python syntax check

**59 tests passed in a combined offline repository test environment.** These are mocked API regression checks, *not* evidence of a successful live deployment on your Mac.

There is no automatic push to GitHub and no GitHub Actions workflow.
