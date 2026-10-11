# Banco Aurora source synchronization fix v4

## What failed

The v3 `sync-managed-agent-source.py` generates app bundles in an isolated temporary
repository tree and copies `agents`, `banking_common`, `knowledge`, and requirements.
However, the real `deployment/agent-manager/scripts/prepare-apps.py` additionally
builds the supporting MCP and external-risk bundles, and references `mcp_server`.
Omitting that source directory caused:

```
FileNotFoundError: .../aurora-generated-.../mcp_server
```

## Fix

`mcp_server` is now copied **into the temporary snapshot only** before running
the existing production generator. The `check` mode does not mutate your Git
checkout, commit, or push anything. This patch does not change the MCP or agent
source code, Agent Manager, your database, or tunnel processes.

## Install

From the repo root:

```bash
unzip -o ~/Downloads/Banco-Aurora-Agent-Source-Sync-v4.zip
python3 scripts/sync-managed-agent-source.py check
```

If `check` reports generated-app differences or dirty agent source, this is a
**different and expected preflight result**, not the temporary-folder error.
Review its listed files before authorizing a source commit/push:

```bash
AURORA_AGENT_SOURCE_PUBLISH=1 ./scripts/start-everything.sh up
```

Use the opt-in publish command only if your current `main` checkout and Git
origin are correct and you approve pushing the listed agent source changes.
The sync script rejects unrelated staged changes, suspicious credential patterns,
and divergent Git history. It cannot guarantee that automated secret scanning
will detect every kind of credential.

## Regression coverage

The tests use isolated temporary local Git remotes. A new test runs the actual
`prepare-apps.py`, including its MCP upstream generation, to catch missing
source dependencies in future changes.

```bash
python3 -m unittest discover -s tests -p 'test_source_sync.py' -v
```

The tests do not contact GitHub or change your actual repository.
