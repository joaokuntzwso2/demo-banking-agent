# Banco Aurora — Managed LLM binding reconciliation v11

Fixes Stage 5 error: `aurora-card already has a different LLM model binding` when the org-level provider handle is `aurora-openai` but the Agent Manager-created model configuration is named `openai`.

Agent Manager supports `--llm-provider` on `amctl agent create`, which can auto-create a template-named model configuration. The configuration name is **not** the same identifier as the provider handle. Existing scripts incorrectly compared the two.

## Apply (from repository root)

```bash
unzip -o ~/Downloads/Banco-Aurora-LLM-Binding-Fix-v11.zip
python3 -m py_compile scripts/aurora-full-reconcile.py
python3 -m unittest discover -s tests -p 'test_llm_binding_v11.py' -v
python3 -m unittest discover -s tests -p 'test_openai_provider.py' -v
unset AURORA_AGENT_SOURCE_PUBLISH
./scripts/start-everything.sh agents
```

### Resolution and safety

- Fetch LLM config summary from `/orgs/default/projects/banco-aurora/agents/<agent>/model-configs`.
- Fetch detail for its exact UUID.
- Verify `envModelConfig.default.llmProxy.providerName == aurora-openai`, even if config name is `openai` or `openai-2`.
- If the correct provider is already attached, **reuse it without POST**.
- If its runtime URL/API-key mapping names are wrong, update *only* `environmentVariables` with PUT and verify by fetching the detail again.
- If the configured provider is genuinely different, detail is missing, or there are multiple LLM configs, abort with explicit diagnostics; no automatic provider replacement, deletion, or credential rotation.
- If no LLM model configuration exists, attach the declared provider once through the existing create request and verify.

This patch is **local-only**. It does not commit/push Git code, enable GitHub Actions, rebuild images, rotate credentials, or recreate the MCP Proxy. It does not touch agent source files. The `agents` command may of course attempt managed-agent deployment and build operations after this step.

## Read-only inspection if a genuinely different provider is reported

The command below prints configuration names and referenced providers, but not API keys:

```bash
amctl api /orgs/default/projects/banco-aurora/agents/aurora-card/model-configs \
  | python3 -c 'import json,sys; x=json.load(sys.stdin); d=x.get("data",x) if isinstance(x,dict) else x; v=d.get("configs",[]) if isinstance(d,dict) else d; print(json.dumps([{k:r.get(k) for k in ("uuid","name","type")} for r in v],indent=2))'
```

For a configuration with a returned UUID, fetch the record separately (do not post secrets):

```bash
amctl api /orgs/default/projects/banco-aurora/agents/aurora-card/model-configs/CONFIG_UUID \
  | python3 -c 'import json,sys; r=json.load(sys.stdin); r=r.get("data",r); print(json.dumps({"name":r.get("name"),"type":r.get("type"),"envProviders":{e:(v.get("llmProxy") or {}).get("providerName") for e,v in (r.get("envModelConfig") or {}).items()},"envVariableNames":r.get("environmentVariables")},indent=2))'
```

Replace `CONFIG_UUID` with the actual UUID from the list. A provider mismatch is intentionally not overwritten automatically.

## Validation

Offline tests cover template-derived names, correct-provider reuse, true mismatches, absent/duplicate configs, mapping repair with read-back, missing persistence, and compatibility with previous OpenAI and MCP tests. These are **mocked tests**; verification against the running on-prem WSO2 Agent Manager must be done on your Mac.
