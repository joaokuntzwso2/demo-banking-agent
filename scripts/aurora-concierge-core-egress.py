#!/usr/bin/env python3
"""Create/verify one narrow Concierge->Aurora Core NetworkPolicy.

No existing OpenChoreo or sandbox NetworkPolicy is changed. Source namespace is
resolved from the live, ready Concierge Pod. Refuses ambiguous/missing topology.
"""
import argparse
import json
import subprocess
import sys

CTX = "k3d-amp-local"
CORE_NS = "aurora-demo"
CORE_SERVICE = "aurora-core"
PORT = 8100
POLICY_NAME = "aurora-concierge-core-egress"
SOURCE_SELECTOR = {
    "openchoreo.dev/component": "aurora-concierge",
    "openchoreo.dev/project": "banco-aurora",
    "openchoreo.dev/environment": "default",
}
TARGET_SELECTOR = {"app": "aurora-core"}


def kubectl(*args, input_text=None):
    cmd = ["kubectl", "--context", CTX, *args]
    result = subprocess.run(cmd, text=True, input=input_text, capture_output=True, timeout=35)
    if result.returncode:
        # Do not print stdout/stderr from arbitrary cluster resources; may include secrets.
        raise RuntimeError(f"kubectl {args[0]} failed (exit {result.returncode}); inspect Kubernetes credentials, permissions and context")
    return result.stdout


def kjson(*args):
    try:
        value = json.loads(kubectl(*args, "-o", "json"))
    except json.JSONDecodeError as exc:
        raise RuntimeError("Invalid Kubernetes JSON response") from exc
    if not isinstance(value, dict):
        raise RuntimeError("Invalid Kubernetes resource type")
    return value


def select_concierge_namespace():
    selector = ",".join(f"{k}={v}" for k, v in SOURCE_SELECTOR.items())
    pods = kjson("get", "pods", "-A", "-l", selector).get("items", [])
    matches = []
    for p in pods:
        m, s, spec = p.get("metadata", {}), p.get("status", {}), p.get("spec", {})
        if m.get("deletionTimestamp") or not m.get("name", "").startswith("aurora-concierge-"):
            continue
        if s.get("phase") != "Running":
            continue
        containers = spec.get("containers", [])
        statuses = s.get("containerStatuses", [])
        if not any(c.get("name") == "main" for c in containers):
            continue
        if not any(c.get("name") == "main" and c.get("ready") is True for c in statuses):
            continue
        matches.append((m.get("namespace"), m.get("name")))
    if len(matches) != 1 or not matches[0][0]:
        raise RuntimeError(f"Expected exactly one ready managed Concierge Pod, found {len(matches)}; refusing policy creation")
    ns, name = matches[0]
    labels = kjson("get", "namespace", ns).get("metadata", {}).get("labels", {})
    if labels.get("kubernetes.io/metadata.name") != ns:
        raise RuntimeError("Source namespace lacks the standard Kubernetes namespace-name label")
    return ns, name


def verify_core():
    ns = kjson("get", "namespace", CORE_NS)
    if ns.get("metadata", {}).get("labels", {}).get("kubernetes.io/metadata.name") != CORE_NS:
        raise RuntimeError("Core namespace lacks Kubernetes namespace-name label")
    svc = kjson("-n", CORE_NS, "get", "svc", CORE_SERVICE)
    ports = svc.get("spec", {}).get("ports", [])
    allowed = [p for p in ports if p.get("port") == PORT and p.get("targetPort") in (PORT, str(PORT)) and p.get("protocol", "TCP") == "TCP"]
    if len(allowed) != 1 or svc.get("spec", {}).get("selector") != TARGET_SELECTOR:
        raise RuntimeError("Aurora Core Service port or Pod selector differs from reviewed topology")
    eps = kjson("-n", CORE_NS, "get", "endpointslices", "-l", "kubernetes.io/service-name=" + CORE_SERVICE)
    addresses = {
        a for obj in eps.get("items", [])
        for endpoint in obj.get("endpoints", [])
        if endpoint.get("conditions", {}).get("ready") is not False
        for a in endpoint.get("addresses", [])
    }
    if not addresses:
        raise RuntimeError("Aurora Core has no ready EndpointSlice addresses")
    pods = kjson("-n", CORE_NS, "get", "pods", "-l", "app=aurora-core")
    matching = [p for p in pods.get("items", []) if p.get("status", {}).get("podIP") in addresses and any(c.get("ready") is True for c in p.get("status", {}).get("containerStatuses", []))]
    if not matching:
        raise RuntimeError("Aurora Core EndpointSlice is not backed by a ready selected Pod")


def desired_policy(namespace):
    return {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "NetworkPolicy",
        "metadata": {
            "name": POLICY_NAME,
            "namespace": namespace,
            "labels": {"app.kubernetes.io/managed-by": "aurora-startup"},
        },
        "spec": {
            "podSelector": {"matchLabels": SOURCE_SELECTOR},
            "policyTypes": ["Egress"],
            "egress": [{
                "to": [{
                    "namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": CORE_NS}},
                    "podSelector": {"matchLabels": TARGET_SELECTOR},
                }],
                "ports": [{"protocol": "TCP", "port": PORT}],
            }],
        },
    }


def lookup_policy(namespace):
    cmd = ["kubectl", "--context", CTX, "-n", namespace, "get", "networkpolicy", POLICY_NAME, "-o", "json"]
    result = subprocess.run(cmd, text=True, capture_output=True, timeout=35)
    if result.returncode:
        # Only a canonical Kubernetes NotFound on an object GET means absence.
        if "NotFound" in result.stderr or "not found" in result.stderr.lower():
            return None
        raise RuntimeError("Cannot determine existing NetworkPolicy state; refusing modification")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Malformed NetworkPolicy response") from exc


def verify_policy(existing, wanted):
    if not isinstance(existing, dict) or existing.get("spec") != wanted["spec"]:
        raise RuntimeError("A policy with the target name differs from the restricted contract; refusing to overwrite it")
    if existing.get("metadata", {}).get("labels", {}).get("app.kubernetes.io/managed-by") != "aurora-startup":
        raise RuntimeError("Existing policy is not owned by Aurora startup; refusing to take ownership")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("ensure", "verify"))
    args = parser.parse_args()
    try:
        namespace, pod = select_concierge_namespace()
        verify_core()
        wanted = desired_policy(namespace)
        existing = lookup_policy(namespace)
        if existing is None:
            if args.action == "verify":
                raise RuntimeError("Restricted Core egress permission missing. Run ./scripts/start-everything.sh up to provision it")
            kubectl("create", "-f", "-", input_text=json.dumps(wanted))
            print("AURORA_CORE_EGRESS=created (TCP 8100 only, Concierge to aurora-demo/app=aurora-core)")
        else:
            verify_policy(existing, wanted)
            print("AURORA_CORE_EGRESS=reused (restricted policy already exists)")
        current = lookup_policy(namespace)
        verify_policy(current, wanted)
        print(f"AURORA_CORE_EGRESS=verified; namespace={namespace}; ready_concierge={pod}")
    except (RuntimeError, subprocess.TimeoutExpired, OSError) as exc:
        print("AURORA_CORE_EGRESS=failed: " + str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
