# Banco Aurora — Managed OpenAI SDK compatibility v10

## Evidence and scope

- The running Concierge returns 503 when `AsyncOpenAI` is invoked, even though an authenticated `/chat/completions` call made from a temporary container **in the same Concierge Pod network** returned HTTP 200 with a valid completion.
- Canonical Git revision `634794269c3f` declares `openai>=1.50,<3`, and the managed branch in `agents/common.py` passes `api_key=''` to `AsyncOpenAI` alongside the correct `API-Key` gateway credential in `default_headers`.
- OpenAI Python SDK 2.34.0 (released during 2026) changed its constructor so empty API keys are rejected with `OpenAIError: Missing credentials`. This is a strong likely root cause, **not confirmation of the currently installed SDK version**. Relevant public issue: https://github.com/openai/openai-python/issues/3224 .
- This repair uses non-secret `api_key='gateway-only-placeholder'` only as a constructor compatibility token and **retains the existing gateway `API-Key` and empty `Authorization` header**. The provider secret is not in the source.
- The patch adds error-class/status-only logging to Concierge's already-fail-closed `HTTPException(503)` path, so a remaining error can be classified from logs without exposing raw exceptions, request bodies, credentials, or URLs.

## Install, inspect, and patch locally

```bash
cd /Users/joaoluiskuntz/wso2mi/Projects/Demo/demo-banking-agent
unzip -o ~/Downloads/banco-aurora-managed-llm-sdk-v10.zip -d .
python3 -m py_compile scripts/patch-aurora-openai-sdk-compat.py
python3 -m unittest discover -s tests -p 'test_aurora_openai_sdk_compat_v10.py' -v
python3 scripts/patch-aurora-openai-sdk-compat.py
python3 scripts/patch-aurora-openai-sdk-compat.py --apply
git diff --check
git diff -- agents/common.py agents/concierge.py
python3 -m py_compile agents/common.py agents/concierge.py
```

The tool is read-only by default. `--apply` changes **only** the two canonical files and refuses already-modified source; it does not touch generated folders, Git remote, secrets, settings, builds, or Kubernetes. It is idempotent.

## Explicit source publishing and managed build

This repository uses `scripts/sync-managed-agent-source.py ensure` to regenerate all six agent app bundles from canonical source; it commits and pushes source only if you explicitly opt in. Review the diff and any existing staged changes first:

```bash
git diff --cached --name-only
# Should be empty. Review local changes before opting into a Git push.
AURORA_AGENT_SOURCE_PUBLISH=1 ./scripts/start-everything.sh agents
```

The source publisher checks your `main` branch, upstream SHA and scoped file set before committing/pushing. All six agents include the shared client code and therefore may require rebuilt managed images. Previously successful builds of the old SHA cannot contain this source fix. **Never manually mutate Agent Manager-managed pods or Kubernetes Secrets to substitute for publishing source.**

A failure in another stage is not permission to delete, reset, or rotate anything. Examine the returned failure before proceeding.

## Confirmation

```bash
python3 scripts/diagnose-aurora-chat.py
python3 scripts/diagnose-aurora-upstream.py --since 5m --include-core --include-portal
```

For success, Portal `/api/chat` and Concierge `/chat` should return 200 with valid content, then run the six executive scenarios and mandatory gateway-denial check via `./scripts/start-everything.sh agents`. The in-pod model HTTP 200 alone does **not** prove the running SDK path works.

If Concierge still returns 503, its new log line identifies only the SDK exception class and HTTP status; look for `Governed LLM invocation failed` in the Agent Manager Concierge logs. Do not paste raw environment variables, Kubernetes Secret values, gateway keys, prompt bodies, or full response bodies.

## Scope and guarantees

This patch is intended for the Git source confirmed at commit prefix `634794269c3f`. It refuses unknown source patterns. Local tests verify the textual change and Python 3.11 grammar, but do not execute the live OpenAI SDK or WSO2 gateway; validation must happen in your actual cluster. The working narrow Concierge -> Core TCP/8100 NetworkPolicy is not modified.
