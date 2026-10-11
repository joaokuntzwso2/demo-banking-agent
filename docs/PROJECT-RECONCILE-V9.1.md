# Banco Aurora v9.1 — Python 3.11 syntax hotfix

This is a minimal correction to the v9 reconciler and does not change any API
request or deployment behavior. It resolves the Python 3.11 error at line 335:
`SyntaxError: f-string expression part cannot include a backslash`.

The v9 expression included a regular-expression literal with `\b` escape
sequences directly inside an f-string expression. v9.1 calculates `is_conflict`
outside the f-string, then interpolates only that boolean.

## Install from repository root

Check the **broken v9** checksum first:

```sh
shasum -a 256 scripts/aurora-full-reconcile.py
# Expected pre-install: 7bbfe717ae57612874440c340828cc8e1e0c4c47fd84c15e4534f7b7606d03ac
```

If it matches:

```sh
cp scripts/aurora-full-reconcile.py ~/Downloads/aurora-full-reconcile.before-v9.1.py
unzip -o ~/Downloads/banco-aurora-project-reconcile-v9.1.zip -d .
python3 -m py_compile scripts/aurora-full-reconcile.py
python3 -m unittest discover -s tests -p 'test_project_reconcile_v9.py' -v
python3 -m unittest discover -s tests -p 'test_python311_compat_v91.py' -v
git diff --check
```

Expected patched SHA256: `61266dab1b8e3c3c25df455ce65108bc585b4c792cd084860ef4fa62d9be7467`

Only the reconciler, an additive test, and this note are included. Your v9 project
tests should already be present. Do not overwrite a reconciler with an unexpected
checksum; review any newer local edits first.

## Validation

- Full available local suite: 228 tests, 1 skipped, no failures, under Python 3.13.
- Static PEP 701 regression test: rejects backslashes inside f-string expressions
  that are invalid in Python 3.11.
- **Python 3.11 was not installed in the test container.** Your local `python3 -m
  py_compile` on Python 3.11 is the required direct syntax verification.
- No live Agent Manager calls were made during testing.

To resume deployment after successful local verification:

```sh
unset AURORA_AGENT_SOURCE_PUBLISH
./scripts/start-everything.sh agents
```

This does not touch secrets or reset project, agents, MCP proxy, builds or pods.
