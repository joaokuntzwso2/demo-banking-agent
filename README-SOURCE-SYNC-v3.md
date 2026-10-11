# Banco Aurora full-stack automation: safe managed agent Git synchronization

This v3 update resolves the Stage 5 error `Agent sources/app folders have uncommitted changes` by **preflighting Git before Stage 1**. WSO2 Agent Manager builds six platform-hosted agents from a **remote Git commit**, not from your Mac's uncommitted files. The `main` branch of the configured remote must contain the standalone application source contexts.

## Install over v2

From the `demo-banking-agent` repository, extract `Banco-Aurora-Full-Stack-Automation-v3.zip` into the repository root. Files in `agent-manager-support` named `support.env`, `secrets.env`, and the tunnel state are not included and are never overwritten.

## Safe read-only inspection

```bash
python3 scripts/sync-managed-agent-source.py check
```

This regenerates six agent application bundles **only in a temporary directory** to compare them against your tracked folders. It also compares local Git HEAD to `origin/main` and reports dirty managed-source paths. It never commits, pushes, or overwrites the checkout.

## Fully integrated startup

```bash
./scripts/start-everything.sh up
```

If all relevant sources are clean and match `origin/main`, startup runs without a Git write. If generated bundles or source are dirty, startup now fails at **Stage 0** with the relevant paths and an explicit command to authorize **source-only** synchronization:

```bash
AURORA_AGENT_SOURCE_PUBLISH=1 ./scripts/start-everything.sh up
```

This opt-in is required because it **commits and pushes public source to GitHub**. It is not acceptable for a demo installer to silently publish arbitrary local changes. The permission is per command; subsequent clean runs require no flag.

When authorized, the script:

1. Confirms it is on the expected Git branch and its `origin` matches the configured repository (HTTPS and GitHub SSH URLs supported).
2. Refuses to combine previously staged work or to force-push a diverged branch.
3. Reconstructs six application folders from canonical `agents`, `banking_common`, `knowledge`, and `requirements.txt` source in a temporary tree.
4. Creates a local archive backup of the existing six app bundles at `agent-manager-support/.source-sync-backups/`; those files are Git-ignored.
5. Updates *only* the six generated managed-agent app folders in the working checkout. The optional upstream MCP and external Risk advisor folders are left alone.
6. Stages **only** `agents`, `banking_common`, `knowledge`, `requirements.txt`, and `deployment/agent-manager/apps`. Rejects unexpected files, symlinks, a predefined set of key/token patterns, and oversized files. This is a precaution, not a guarantee that arbitrary secrets cannot be present in source. Review your public source before opting in.
7. Commits with `chore(aurora): sync managed agent build inputs`, pushes to the configured branch by **fast-forward only**, and verifies the remote SHA.
8. Resumes an interrupted push when the local commit matches the recorded source-only publication state.
9. Continues Stage 1–6 only after the remote and local app sources are verified in sync.

**Security**: Never include actual customer data, private keys, or credentials in the canonical managed-agent sources. `support.env`, `secrets.env`, the Cloudflare origin key, local state files and unrelated changes are not staged. The archive backup stays on your Mac and is mode 0600. The script does not log secret values.

**No guarantee of completed deployment**: After Stage 0, Agent Manager's v1.0.0 tool-binding and gateway key REST operations, actual agent build/deploy status, and required gateway-level negative authorization tests must still succeed. The launcher correctly fails if any release-specific operation is unsupported or unverified. For uninterrupted demos, replace the temporary Cloudflare Quick Tunnel with a stable authenticated HTTPS hostname.

## Tests

```bash
bash -n scripts/start-everything.sh
python3 -m unittest discover -s tests -p 'test_source_sync.py' -v
python3 -m unittest discover -s tests -p 'test_reconciler.py' -v
python3 -m unittest discover -s agent-manager-support/tests -v
```
