# Banco Aurora Agent Manager 1.0.0 — verified build-resume refinement

## Scope

This overlay is applied AFTER `banco-aurora-amp100-llm-fix.zip` and does not include original `start-everything.sh`, `sync-managed-agent-source.py`, provider secrets, or cluster manifests. It changes only `scripts/aurora-full-reconcile.py`, an existing build-regression test, and adds a new build-resumption test. The previously fixed `envMappings.default.configuration` LLM reconciliation is unchanged.

## Changes

* A queued, building, or running managed build for the **verified desired 40-character Git SHA** is monitored rather than duplicated. Build identity is pinned when the API exposes one. No `amctl agent build create` command runs in this reuse path.
* An in-flight build missing a verified commit, an inventory read timeout before deciding, unexpected replacement of a build, missing build inventory, unknown status, or failed build is rejected safely.
* The previous `test_build_reuse.py` expectation that every in-flight build must immediately error is updated to prove safe resume and no writes. Nine new regression cases verify the conditions above.

## Verification

The previous ZIP's reconciler SHA256 is:

`bf7b0e270e02fb44376881700098bda4121a2c2da4816d34cbebed3ebdf3cc77a`

Only apply the overlay if the current `scripts/aurora-full-reconcile.py` is that exact version; otherwise inspect its current changes and merge deliberately.

On a test-only extracted overlay, `python -m unittest discover -s tests -p 'test_*.py' -q` passed **86 tests (1 skipped)**. This does not include older files found only in the user's Mac workspace, or tests requiring SQLAlchemy, so do NOT report the Mac's full suite as green.

### Mac commands (after downloading ZIP)

```bash
cd /Users/joaoluiskuntz/wso2mi/Projects/Demo/demo-banking-agent
shasum -a 256 scripts/aurora-full-reconcile.py
cp scripts/aurora-full-reconcile.py ~/Downloads/aurora-full-reconcile.before-build-resume-v2.py
unzip -o ~/Downloads/banco-aurora-amp100-build-resume-v2.zip -d .
python3 -m py_compile scripts/aurora-full-reconcile.py
python3 -m unittest discover -s tests -p 'test_build_resume_v2.py' -v
python3 -m unittest discover -s tests -p 'test_build_reuse.py' -v
python3 scripts/diagnose-aurora-llm.py --agents aurora-account aurora-card
git diff --check
```

After read-only verification of the actual build inventory, resume with:

```bash
amctl agent build list aurora-account --project banco-aurora --org default --json
unset AURORA_AGENT_SOURCE_PUBLISH
./scripts/start-everything.sh agents
```

Do not confuse a successful unit-test run with a completed managed deployment. This environment cannot contact the local Mac's Agent Manager instance.

## Legacy tests in the supplied failure log

The failing older files `test_llm_binding_v11.py`, `test_llm_environment_v12.py`, and `test_build_progress_v10.py` were **not** in the uploaded source ZIP. Without their exact test code, they cannot safely be patched. Key required updates:

* Use the captured `tests/fixtures/amp_100_llm_live.json` mapping (`envMappings.default.configuration`) as the primary v1.0.0 fixture, including providerUuid, proxyUuid, and active status. Do not merely add an error-message assertion while keeping an invalid fixture.
* Older mock configurations without `proxyUuid` cannot be considered verified existing associations; a test expecting those to be reusable should be changed to expect a safe refusal, or updated to a faithful positive fixture.
* Do not reintroduce automatic PUT for a model config with no environment mapping, even if the display name looks OpenAI-related. That prior behavior was unsafe.
* Update assertions that demand the retired error text `lacks envModelConfig` or `targets provider` to assert the relevant invariant and absence of PUT/POST calls.
* Build tests must account for the new read-only inventory check before creating any build, and use a real build list response shape. A previously queued build is not permission to issue a duplicate create.
* `test_core.py` and `test_executive_scenarios.py` fail at import because SQLAlchemy is not installed in that Python environment. Use your project's declared dependency lock/requirements in a virtual environment; do not fix this by suppressing these tests.

## No writes until live verification

The existing reconciliation workflow can still create genuinely missing agents and model configs after read-only checks. This overlay only changes what happens when a managed build already exists. It does not bypass OAuth, adjust provider credentials, reissue gateway keys, or delete any deployment resources.
