# Banco Aurora — Reconciler test isolation v5.1

This small patch changes **only** `tests/test_reconciler.py`.

The old `test_reject_incomplete_scopes` uses a synthetic `https://demo.example/mcp` URL,
but `verify_proxy()` also reads `agent-manager-support/.mcp-public-tunnel/url`
from the local checkout. When a live tunnel has been started, the test fails on
that mismatch instead of reaching its intended 15-scopes assertion.

The fixed test patches `SUPPORT` to a temporary directory, retaining the
production tunnel-URL safety check. A separate regression test proves mismatched
live-tunnel URLs still fail closed.

Apply from repo root:

```sh
unzip -o ~/Downloads/Banco-Aurora-Agent-Tests-Fix-v5.1.zip
python3 -m unittest discover -s tests -p 'test_reconciler.py' -v
unset AURORA_AGENT_SOURCE_PUBLISH
./scripts/start-everything.sh agents
```

No application scripts, secrets, cluster resources, Git remotes, or GitHub Actions
settings are changed. The `agents` command can still encounter further live
Agent Manager API errors, which should be diagnosed from its redacted output.
