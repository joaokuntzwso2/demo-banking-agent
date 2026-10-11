# Banco Aurora — Concierge → Core network diagnosis

This package does not change the reconciler or any managed agent resources.

Install from the repo root:

```bash
unzip -o ~/Downloads/banco-aurora-core-network-diagnostics-v1.zip -d .
python3 -m py_compile scripts/diagnose-aurora-core-path.py
python3 -m unittest discover -s tests -p 'test_aurora_core_path_diagnostic.py' -v
```

Read-only preflight:

```bash
python3 scripts/diagnose-aurora-core-path.py
```

Optional active network probe:

```bash
python3 scripts/diagnose-aurora-core-path.py --probe
```

`--probe` adds **one ephemeral BusyBox container to the running Concierge Pod**. It shares the Pod's network namespace and performs only unauthenticated `GET /health` requests to Core by FQDN, Service ClusterIP, and ready endpoint IP. It doesn't restart the application or read credentials. The ephemeral container record remains in Pod metadata until that Pod is replaced, so this option is NOT fully read-only. It requires appropriate Kubernetes RBAC and the image to be available. The cached image observed in this k3d cluster is `rancher/mirrored-library-busybox:1.36.1`.

If every network probe passes while the Concierge application still raises `httpx.ConnectError`, inspect application-specific proxy environment, the *effective process* `BANK_CORE_URL` and whether the traceback points to another outbound target. Don't change network policies on speculation.

If both Service IP and endpoint IP fail, inspect NetworkPolicies in the Concierge and Core namespaces. Do not add a broad allow-all policy. Check Kubernetes platform-generated policies and agent-specific egress requirements before designing narrow exceptions.

Do not paste secrets, complete Pod environment variables, or raw logs into a chat.
