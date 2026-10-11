# Banco Aurora — AgentID role-assignment contract fix (AMP 1.0.0)

## Cause

The old `ensure_identity_roles()` passed the GET `.../roles/{id}/assignments` response to `collection(..., ('assignments',))`. In WSO2 Agent Manager 1.0.0, the **GET** response (`thundersvc.AgentRoleAssignments`) has `agents` and `groups`, both `omitempty`; the **POST** `/assignments/add` request has `assignments`. Consequently, even a successful existing or newly created direct agent grant appeared absent in the old verifier.

The patched method parses and validates `agents[]` separately, uses the environment-specific `thunderAgentId` (never conflates it with a management-plane agent `id`), checks all five role/identity pairs and assignment collections before the first write, performs only a missing direct role grant, then verifies by GET. It does not delete or replace any role, group, agent, LLM provider, MCP proxy, build, deployment, or gateway credential.

### Read-only operator commands

```bash
amctl api -X GET /orgs/default/environments/default/agent-identities/roles
amctl api -X GET /orgs/default/environments/default/agent-identities/agents
# Identify the ID of role 'aurora-account' from the first response, then:
amctl api -X GET /orgs/default/environments/default/agent-identities/roles/<account-role-id>/assignments
```

An example GET response shaped according to the Go DTO (not a claim about the live Banco Aurora response):

```json
{"agents":[{"id":"<thunder-agent-id>","type":"agent"}],"groups":[]}
```

An empty assignment response can legitimately be `{}` because the fields are `omitempty`. A role assignment request (only if absent and uniquely verified) is:

```json
{"assignments":[{"id":"<thunder-agent-id>","type":"agent"}]}
```

### Regression tests and installation

From a repository currently using `banco-aurora-amp100-build-lock-v4.zip`:

```bash
cd /Users/joaoluiskuntz/wso2mi/Projects/Demo/demo-banking-agent
shasum -a 256 scripts/aurora-full-reconcile.py
# Must match 3496804ab1f3b0e0495bf9713f1de5a9d30a5f3b1e82524ec30dc424cee2ff1a
cp scripts/aurora-full-reconcile.py ~/Downloads/aurora-full-reconcile.before-v5.py
unzip -o ~/Downloads/banco-aurora-amp100-agentid-contract-v5.zip -d .
python3 -m py_compile scripts/aurora-full-reconcile.py
python3 -m unittest discover -s tests -p 'test_agentid_contract_v5.py' -v
python3 -m unittest discover -s tests -p 'test_build_lock_v4.py' -v
python3 -m unittest discover -s tests -p 'test_live_llm_reconciliation.py' -v
git diff --check
unset AURORA_AGENT_SOURCE_PUBLISH
./scripts/start-everything.sh agents
```

Do not apply v5 blindly if the installed reconciler's SHA differs from the supplied v4 SHA. Merge/review in that case. The ZIP contains only the existing Python reconciler, one test, and this documentation; no secrets, agent build bundles, cluster manifests, GitHub Actions, or source-publishing changes.

### Safety cases

* Already assigned: only GETs, repeated runs are read-only.
* Truly absent: exactly one POST to the confirmed role; read-back must show the exact `thunderAgentId` with `type:agent`.
* Group assignments do not count as direct assignment. Group membership is not modified.
* No assignees: both `{}` and `{"agents":[],"groups":[]}` are supported.
* Concurrent POST conflict: only accept 409 after an independent GET proves that exact direct grant now exists; do not blindly retry.
* Eventual consistency: up to three GETs with brief pauses; never a duplicate POST.
* Malformed or unknown GET response, duplicate identity/role, project conflict, missing ThunderID, wrong/failed GET, 403, unrelated 409: fail closed.
* `check` now calls `ensure_identity_roles(read_only=True)` and cannot create a role assignment.

### Remaining gates not implied by this fix

Role assignment existence **does not prove correct scope grants** or effective token authorization. Review each role's permissions and perform an actual allowed and denied MCP tool-call test under an AgentID token; the preexisting `AURORA_MCP_AUTHZ_VERIFY_CMD` gate still requires it. Deployment status logs are management-plane checks, not proof that all six runtime endpoints and executive scenarios passed. `aurora-concierge` and Portal remain to be completed on the live environment.

### Source references

- https://wso2.com/agent-platform/docs/v1.0.0/guides/authorize-agent-access-to-mcp-tools/
- https://pkg.go.dev/github.com/wso2/agent-manager/agent-manager-service@v0.0.0-20260901095814-41e1ff4ad525/clients/thundersvc#AgentRoleAssignments
