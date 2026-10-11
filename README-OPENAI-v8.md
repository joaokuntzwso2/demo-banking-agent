# Banco Aurora — OpenAI LLM service-provider and binding fix (v8)

Install from the **Banco Aurora repository root**:

```sh
unzip -o ~/Downloads/Banco-Aurora-OpenAI-Provider-Fix-v8.zip
python3 -m py_compile scripts/aurora-full-reconcile.py
python3 -m unittest discover -s tests -p 'test_reconciler.py' -v
python3 -m unittest discover -s tests -p 'test_openai_provider.py' -v
unset AURORA_AGENT_SOURCE_PUBLISH
./scripts/start-everything.sh agents
```

This is an **overlay** for the existing full-stack installation, after the Agent
Creation/Base Path v7 fix. It replaces `scripts/aurora-full-reconcile.py` and
adds/updates focused tests only. It deliberately does not replace
`start-everything.sh`, Git files, support.env, database, or credentials.

On the first run (if `aurora-openai` does not exist), the CLI prompts:

```
OpenAI API key (input hidden):
```

Paste the key **only into that terminal prompt**. It passes through the subprocess
stdin to `amctl llm-provider create --api-key-stdin`; it is never put in argv,
print output, application environment variables, Git, or a local credential
file. On reruns, an existing verified OpenAI provider is reused without
prompting. For rotation, use the platform provider settings; this patch does
not replace an existing provider or reset credentials.

Defaults (overridable as environment variables):

* `AURORA_LLM_PROVIDER=aurora-openai`
* `AURORA_LLM_GATEWAY=api-platform-default-default`
* `AURORA_AMP_ORG=default`, `AURORA_AMP_ENV=default`

The provider uses the built-in `openai` template, the organization AI Gateway,
`/aurora-openai` context, and version `v1.0`.

For **new** agents, `amctl agent create` attaches the provider with
`--llm-provider aurora-openai --llm-url-env LLM_PROVIDER_URL --llm-api-key-env LLM_PROVIDER_KEY`.
For **existing** agents (including your existing `aurora-account`), the
reconciler creates a `type=llm` model configuration using the provider for
the `default` environment and verifies runtime variables. It sets
`USE_LLM_PROVIDER=true` and `REQUIRE_GOVERNED_LLM=true` at deployment. The
OpenAI upstream API key never reaches agent secrets; platform-managed
provider connection credentials are injected automatically.

## Fix for earlier `Type must be llm`

The earlier reconciler posted `type=mcp` to `/model-configs`. The installed
Agent Manager treats that endpoint as **LLM-only**. This version does not use
`/model-configs` for MCP; it probes only dedicated tool/MCP configuration
routes for MCP binding and fails with an explicit message if none are
available. **This distinction is verified offline, but the actual dedicated
MCP route and complete runtime flow still require live testing against your
Agent Manager 1.0.0.** Do not disable gateway validation or bypass MCP scopes.

## Important

* GitHub repository remains source-only; there are **no GitHub pushes**, no
  commits, and no GitHub Actions introduced by this patch.
* No automatic agent deletion, provider overwrite or key rotation.
* Agent Manager 1.0.0 REST binding calls need confirmation on your running
  Mac. Do not claim all six agents are deployed until the launcher finishes
  its gateway-level and end-to-end checks.
