# Banco Aurora — one-command local WSO2 Agent Manager bootstrap

This is a **repo-local macOS launcher** for WSO2 Agent Manager **1.0.0**, using its official quick-start container on a dedicated Colima Docker daemon. It is designed for Banco Aurora but does not require or alter the banking application source.

## Install once into your existing repository

Unzip `Banco-Aurora-Agent-Manager-Bootstrap.zip` into the existing `demo-banking-agent/` directory. The package contributes:

```
scripts/start-agent-manager.sh
docs/AMP-LOCAL-BOOTSTRAP.md
tests/agent-manager-bootstrap/test_bootstrap.sh
```

The package intentionally contains **no** `.git`, `.env`, secrets, Kubernetes state, or generated identities. Review the script before running it. Requires macOS, installed `colima`, `docker`, `k3d`, `kubectl`, and `curl`.

## Start/reuse everything

If you are inside the official `wso2-amp:~$` installer shell, type `exit` to return to your Mac terminal first. From the project root:

```bash
bash scripts/start-agent-manager.sh up
```

It does the following, using idempotent checks:

1. Starts the **existing** Colima `agent-manager` profile *without changing any VM sizing*. If absent, creates one using Apple virtualization, Rosetta, network-address, 8 CPU, 16 GiB memory and 100 GiB disk. Configure sizing via environment variables **before the first creation only**.
2. Switches Docker to `colima-agent-manager`, separate from your other Colima profiles.
3. Checks for the `amp-local` k3d cluster. If present, starts/reuses it and **does not rerun the installer**. If absent, runs `./install.sh` inside the pinned `ghcr.io/wso2/amp-quick-start:v1.0.0` image, following WSO2's official direct-container quick start.
4. Backs up `~/.kube/config` and merges the cluster's current certificates/context.
5. Checks the Kubernetes node and Agent Manager API/Console deployments.
6. Installs `amctl` v1.0.0 from WSO2 only if needed.
7. Prints Console/API URLs and the next command for Banco Aurora.

**On your Mac today, the existing v1.0.0 installation should be detected and reused.** It is already installed and healthy, based on your latest terminal log. It is not necessary to reinstall or create another Colima VM.

WSO2 quick-start docs: https://wso2.com/agent-platform/docs/v1.0.0/get-started/quick-start/
WSO2 amctl docs: https://wso2.com/agent-platform/docs/v1.0.0/guides/cli-installation/

### Optional first-time sizing

Only if the profile does not exist (do not resize an existing profile):

```bash
AMP_COLIMA_CPU=8 AMP_COLIMA_MEMORY=16 AMP_COLIMA_DISK=100 \
  bash scripts/start-agent-manager.sh up
```

Ensure your Mac has enough physical RAM; running multiple Colima VMs simultaneously can exhaust host memory. Stop unneeded profiles manually if necessary; this script never stops or deletes other profiles.

### Authentication

After a **fresh** installation, the `amctl` login is interactive and must be completed by you. Existing logins are retained:

```bash
export PATH="$HOME/.local/bin:$PATH"
amctl login --url http://api.amp.localhost:8080
amctl context show
```

If already logged in (as shown in your latest output), skip this step.

### Start Agent Manager **and** the Banco Aurora MCP backend

First ensure the separate MCP autostart kit is installed under `agent-manager-support/`, and `agent-manager-support/support.env` is correctly configured (k3d cluster `amp-local`, context `k3d-amp-local`, target `kubernetes`, and your org `default`).

Then a single command starts/reuses AMP and deploys/publishes the Banco Aurora support stack:

```bash
bash scripts/start-agent-manager.sh up --with-banking
```

Or, if AMP is already healthy, start only the banking services and publish MCP:

```bash
bash scripts/start-agent-manager.sh banking
```

This second stage calls the *existing* `agent-manager-support/start.sh`, which provisions PostgreSQL, Banking Core, upstream MCP and registers the governed MCP Proxy via `amctl`. **It does not deploy the six managed agents.** Those are intentionally configured manually in the Agent Manager Console.

### Check status and open Console

```bash
bash scripts/start-agent-manager.sh status
bash scripts/start-agent-manager.sh console
```

Console: http://console.amp.localhost:8080
API: http://api.amp.localhost:8080

## Error behavior and data safety

- The script deliberately **does not use** `k3d cluster delete`, `colima delete`, `kubectl delete`, `docker system prune`, or any VM resizing.
- If an existing cluster fails Kubernetes readiness, it reports the K3s server logs and exits instead of trying an unsafe reinstallation.
- It always refreshes kubeconfig credentials from the actual k3d cluster after backing up your previous file.
- If the official first-time installer fails, it stops immediately and preserves resulting Docker/Kubernetes state for diagnosis.
- The installer may print newly generated identity-provider credentials. Treat its terminal output as sensitive and do not commit logs.
- If credentials were pasted into a public channel or shared document, rotate them before treating the installation as trusted.

## Local source smoke test

This checks control flow using fake Docker/Colima/K3d programs; it makes **no real infrastructure changes**:

```bash
bash tests/agent-manager-bootstrap/test_bootstrap.sh
```

### Known limitations

- Requires Docker, Colima, k3d and kubectl to be installed on the host. It installs **amctl** only. Installing Homebrew/system virtualization drivers is intentionally outside scope.
- Fresh WSO2 installation executes the official Quick Start container via `/bin/bash -lc './install.sh'`. It has not been run against your Mac from this environment, but follows WSO2's documented container invocation and uses the same installer entrypoint you already ran manually.
- `up --with-banking` requires a valid existing `agent-manager-support/support.env` and an authenticated `amctl` context. It does not silently invent organizational identities or publish an unsecured MCP endpoint.
- No script can guarantee recovery of a k3d cluster that has become unusable due to the known Colima network-IP issue; use the diagnostics rather than repeatedly reinstalling.
