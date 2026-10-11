# Banco Aurora — Concierge to Banking Core egress audit

## Evidence

The prior debug probe from the Concierge Pod's shared network namespace reports:
- DNS: ok
- Banking Core FQDN/Service IP/Endpoint IP on HTTP 8100: failed
- 12 NetworkPolicy objects in the OpenChoreo-managed Concierge namespace; none in Core namespace.

This is consistent with an egress isolation rule; it is not proof on its own. BusyBox `wget` failure can also indicate an HTTP or transport failure; it does not distinguish them.

## Installation and preflight

From demo-banking-agent repo root:

```sh
unzip -o ~/Downloads/banco-aurora-core-egress-policy-v1.zip -d .
python3 -m py_compile scripts/diagnose-aurora-core-egress.py
python3 -m unittest discover -s tests -p 'test_aurora_core_egress_v1.py' -v
python3 scripts/diagnose-aurora-core-egress.py
```

Read-only mode discovers the running Concierge pod, identifies an exact stable label, reads Core's Service selector and ready pod, and analyzes **only standard Kubernetes NetworkPolicy** egress rules selecting Concierge. It reports `DENIED`, `ALLOWED`, `UNRESTRICTED` or `UNCERTAIN`.

**Only for `DENIED`**, after reviewing the plan:

```sh
python3 scripts/diagnose-aurora-core-egress.py --apply
python3 scripts/diagnose-aurora-core-path.py --probe
python3 scripts/diagnose-aurora-chat.py
```

The new policy is `aurora-concierge-egress-core-8100`, in Concierge's current namespace, and permits only TCP destination port 8100 **to pods selected by the Core Service selector within namespace `aurora-demo`**, selecting the Concierge pod via a unique stable identity label. It does not modify preexisting policies. Explicit `--apply` requires a Kubernetes server-side dry-run before an actual `create`.

If the audit says `ALLOWED`, `UNRESTRICTED`, or `UNCERTAIN`, it never creates policy, even with `--apply`; investigate other controls or routing. It also refuses selector ambiguity, missing endpoints, and existing policy name collisions.

Kubernetes NetworkPolicies are additive. Adding this scoped egress rule can allow the flow where standard Kubernetes NetworkPolicy blocks it, unless cluster-wide CNI policies or other controls restrict connectivity. No Core ingress policy was present in the prior probe, but verify the current inventory before creating a rule.

## Rollback

Only if this script successfully created the dedicated policy, remove **that one policy**:

```sh
kubectl --context k3d-amp-local -n "<Concierge namespace printed by audit>" \
    delete networkpolicy aurora-concierge-egress-core-8100
```

Do not delete or widen the operator-managed NetworkPolicies. A future OpenChoreo reconcile could change pod labels or policy objects; after verifying the fix, integrate an appropriate connectivity declaration in the supported deployment configuration for durable behavior.

## Security

No credentials, environment variable values, Secret/ConfigMap objects, API calls carrying customer data, or workloads are read or changed. `--apply` creates only one new additive Kubernetes NetworkPolicy, after label and policy verification. This is **not** a replacement for Agent Manager gateway authorization, AgentID, or MCP permissions.
