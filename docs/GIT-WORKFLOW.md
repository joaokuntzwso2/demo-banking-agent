# Source control and GitHub publishing — Banco Aurora

## Repository

Remote: https://github.com/joaokuntzwso2/demo-banking-agent (initially empty).

Commit the **source and build contexts**, not local credentials, workstation databases, cached bytecode, or build products. The generated `deployment/agent-manager/apps/aurora-*` folders are **required Git build contexts**; do not ignore them. Their code is derived from `agents/`, `banking_common/`, and `knowledge/` by `prepare-apps.py`.

## What should be committed

- `.gitignore`, `.dockerignore`, `.env.example`, root `Dockerfile`, `compose.yaml`, `requirements.txt`, `pytest.ini`.
- `README.md`, `docs/` (including this guide and business story), `knowledge/policies.json` (fictional).
- Runtime code: `agents/`, `banking_common/`, `mcp_server/`, `mock_core/`, `demo_portal/`.
- `deployment/agent-manager/apps/aurora-{concierge,account,card,dispute,pix,wellness}/` — generated but **versioned**, independently buildable Git project paths.
- `deployment/agent-manager/upstream-mcp/` — separately buildable MCP service source.
- Deployment manifests, deployment env **examples** (`*.example`), diagnostics, generators, and tests.

Do **not** commit `.env`, non-example `.env.*`, kubeconfig, live API keys, private keys, database files, `__pycache__`, `.pytest_cache`, IDE config, release ZIP archives, or application logs. Example files contain demo-only values/placeholders: replace all credentials for deployment.

## First commit (run on your Mac inside the project)

```bash
cd /path/to/demo-banking-agent
python3 deployment/agent-manager/scripts/prepare-apps.py

# Only needed when no .git directory exists:
git init -b main
# New empty repo — origin shouldn't already be defined:
git remote add origin https://github.com/joaokuntzwso2/demo-banking-agent.git
git remote -v

git add -A
bash scripts/git-preflight.sh
git diff --cached --stat
git diff --cached --name-only
# Check for secrets and unrelated changes before committing.
git commit -m "feat: bootstrap Banco Aurora multi-agent banking demo"
git push -u origin main
```

If `git remote add origin` reports that `origin` already exists, inspect `git remote -v`; use `git remote set-url origin https://github.com/joaokuntzwso2/demo-banking-agent.git` **only when you intend to change it**.

If `git init -b main` reports that Git is already initialized, inspect `git status -sb` and use the existing branch, or create/rename to `main` after evaluating history; do not reset or overwrite commits.

For passwordless authentication, use your installed Git credential helper, GitHub CLI (`gh auth login`) or an approved SSH remote. GitHub no longer accepts account passwords for Git operations over HTTPS.

## Routine updates (especially after editing agents)

```bash
python3 deployment/agent-manager/scripts/prepare-apps.py
git status --short
git add -A
bash scripts/git-preflight.sh
git diff --cached --stat
git commit -m "feat: update banking agent workflows"
git push
```

## Verify exclusions (before the initial commit)

```bash
# Should all report that the file is ignored:
git check-ignore -v .env deployment/agent-manager/.env.concierge.local agents/__pycache__/common.cpython-312.pyc
# These examples must be tracked:
git check-ignore .env.example deployment/agent-manager/.env.concierge.example || true
# Preview staged paths:
git diff --cached --name-only
# Preview staged diff, carefully, without publishing:
git diff --cached
```

`git check-ignore` returns exit status 1 for paths that are **not ignored**; that is correct for the two `.example` paths.

## Security note

`.gitignore` only prevents adding **untracked** files. If sensitive data was ever staged or committed, `.gitignore` will not remove it from Git history; unstage it (`git restore --staged <path>`), or stop tracking it (`git rm --cached <path>`) and rotate any exposed credential. If a secret was pushed, treat it as compromised and address Git history/secrets with your organization’s incident process. The included preflight is a convenience check, not a comprehensive security audit.

The root `.dockerignore` is equally important: the shared Dockerfile uses `COPY . .`, so local `.env` must not enter Docker images even when Git ignores it.
