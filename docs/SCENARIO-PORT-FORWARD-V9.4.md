# Banco Aurora v9.4 — non-conflicting scenario port-forward

## What triggered the change

Stage 6/6 stopped with `Port 8300 already in use`. On the user's Mac,
`sudo lsof -i :8300` showed an **SSH listener** (PID 1214), not an Aurora
application. The prior `verify_scenarios()` unconditionally reserved **local**
port 8300 and stopped if another process already held it.

## Behavior

- Keep the Kubernetes service port unchanged (`svc/aurora-portal`, remote 8300).
- Start a dedicated `kubectl port-forward svc/aurora-portal :8300 --address 127.0.0.1`
  in the existing `k3d-amp-local` context and `aurora-demo` namespace.
- Kubectl selects a free, ephemeral loopback **local** port. This syntax is an
  officially documented Kubernetes operation.
- Read ONLY that subprocess's newly appended log output to discover its own
  selected port; never trust another process's listener, an old log record,
  or a forwarding address bound to a non-loopback interface.
- Use that bound URL in Portal readiness checks and the stateful scenario runner.
- Terminate only the child port-forward launched for the scenarios, even if a
  scenario fails. Preserve SSH tunnels, credentials, agents, and deployments.
- Keep the six scenario assertions and independent AgentID OAuth denial gate.

## Installation

From the repository root, verify that `scripts/aurora-full-reconcile.py`
currently has the **v9.3** SHA-256:

`360a54fbcc0c38747d4fcb863a2f1dceb7de4cd6a088f7579e4565fe1defc128`

Do not overwrite a different version without merging its differences.

```bash
cp scripts/aurora-full-reconcile.py ~/Downloads/aurora-full-reconcile.before-v9.4.py
unzip -o ~/Downloads/banco-aurora-scenario-forward-v9.4.zip -d .
python3 -m py_compile scripts/aurora-full-reconcile.py
python3 -m unittest discover -s tests -p 'test_scenario_port_forward_v94.py' -v
git diff --check
unset AURORA_AGENT_SOURCE_PUBLISH
./scripts/start-everything.sh agents
```

Expected test output will include a line like:

`Portal scenario forwarding confirmed on loopback port 44123 (remote service port 8300).`

The exact local port number is assigned at runtime and will differ.

## Validations and limitations

This is a local, test-environment compatibility fix. It does not alter cluster
resources, migrate the UI, issue new gateway keys, or rebuild managed agents.
The previous sections of the reconciler are unchanged.

Automated local validation: 254 tests run, 1 skipped, no failures using Python
3.13 with AST grammar parsing set to Python 3.11 for the changed file.
This does not certify the live Mac deployment, so final S01–S06 outcomes and
AgentID gateway-denial verification must still execute there.

The strict `check` action also requires a real
`AURORA_MCP_AUTHZ_VERIFY_CMD` negative authorization test. Do not bypass or
replace it with a test that only checks in-application role logic.

Kubernetes documentation:
https://kubernetes.io/docs/tasks/access-application-cluster/port-forward-access-application-cluster/
