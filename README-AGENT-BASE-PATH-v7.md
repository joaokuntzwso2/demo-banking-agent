# Banco Aurora — custom-api base-path correction (v7)

## Observed problem

The `amctl agent create` request was rejected with `spec.inputInterface.basePath is required for subtype custom-api`.

The six platform-hosted FastAPI applications define their routes at the service root (`/chat`, `/health`, and specialist `/execute` or Concierge routes); the correct service base path is `/`.

## Fix

The reconciler now always sends `--base-path /` on each of the six `custom-api` agent creation commands, together with `--port 8000` and `--openapi-spec openapi.json`. It also checks that the installed CLI advertises `--base-path` before passing sensitive environment variables. A regression test confirms the flag and generated API layout.

## Install (repository root)

```bash
unzip -o ~/Downloads/Banco-Aurora-Agent-BasePath-Fix-v7.zip
python3 -m py_compile scripts/aurora-full-reconcile.py
python3 -m unittest discover -s tests -p 'test_reconciler.py' -v
amctl agent create --help | grep -- '--base-path'
unset AURORA_AGENT_SOURCE_PUBLISH
./scripts/start-everything.sh agents
```

No code-generation changes, Git commits or pushes, GitHub Actions, Colima restart, MCP proxy rotation, or database reset are involved. The six agent bundles have already been published to GitHub. This patch is locally test-verified, not live-tested against the user's WSO2 Agent Manager installation.
