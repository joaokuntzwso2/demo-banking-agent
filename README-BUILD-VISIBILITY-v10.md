# Banco Aurora — Managed build diagnostics v10

This patch improves visibility of the Stage 5 build performed **after** Agent Manager verifies the MCP tool binding. It does not change Git, backend pods, credentials, MCP publication, or the actual build/deploy API calls.

## First: diagnose the currently running command (second Mac terminal)

```bash
amctl agent build list aurora-account --project banco-aurora --org default --json | python3 -m json.tool
amctl agent get aurora-account --project banco-aurora --org default --json | python3 -m json.tool
amctl agent build logs aurora-account --project banco-aurora --org default | tail -100
```

The build may be running while the Python reconciler waits silently for up to 20 minutes. Do not start a second `amctl agent build create` while a build is already in progress. If the original Python command is stuck with no managed build and you interrupt it, the managed-side request might still have succeeded, so check the build inventory again before rerunning.

## Apply the local-only diagnostic patch

From the `demo-banking-agent` root (after the original run has finished or been interrupted):

```bash
unzip -o ~/Downloads/Banco-Aurora-Build-Visibility-v10.zip
python3 -m py_compile scripts/aurora-full-reconcile.py
python3 -m unittest discover -s tests -p 'test_build_progress_v10.py' -v
unset AURORA_AGENT_SOURCE_PUBLISH
./scripts/start-everything.sh agents
```

The updated script logs each managed build request, the first available status, subsequent status transitions, and a heartbeat once per minute. It also reports clear timeout diagnostics. The build API and existing retry behavior have *not* been changed, so inspect an in-progress build before restarting to avoid duplicate build requests.

## SQLAlchemy/OpenTelemetry instrumentation incompatibility

Your runtime had SQLAlchemy 2.1.4, but its OpenTelemetry instrumentor wants SQLAlchemy <2.1. This is nonfatal when the application logs confirm Uvicorn startup, but SQLAlchemy spans may be missing.

Change `sqlalchemy>=2.0,<3` to `sqlalchemy>=2.0,<2.1` in the **canonical** repository `requirements.txt` after diagnosing the build. Regenerate and verify all six agent bundles with your existing source-sync tooling, then publish a reviewed source commit only when explicitly approved. That source change takes effect only on rebuilt/redeployed agents. No GitHub Actions are needed; keep them disabled.
