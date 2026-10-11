#!/usr/bin/env python3
"""Read-only diagnostic for existing Banco Aurora model configs (AMP 1.0.0).

No key/proxy credentials are written to disk or printed. All amctl calls are GET
except 'amctl llm-provider list --json' (also read-only).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from aurora_llm_contract import ContractError, classify_binding, parse_collection, parse_provider_registry

ORG = "default"
PROJECT = "banco-aurora"
PROVIDER = "aurora-openai"
AGENTS = ("aurora-account", "aurora-card", "aurora-dispute", "aurora-pix", "aurora-wellness", "aurora-concierge")


def read_json(*args: str) -> object:
    proc = subprocess.run(("amctl", *args), capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        # Avoid echoing arbitrary stderr which can include sensitive data.
        raise ContractError(f"amctl {' '.join(args[:3])} failed (exit {proc.returncode}); inspect locally")
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise ContractError(f"amctl returned invalid JSON for {args[-1]!r}: {exc.msg}") from exc


def get(path: str) -> object:
    return read_json("api", "-X", "GET", path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only AMP v1.0.0 model-config verification")
    parser.add_argument("--agents", nargs="+", choices=AGENTS, default=list(AGENTS),
                        help="Limit audit to agents already created (default: all six)")
    args = parser.parse_args(argv)
    registry = parse_provider_registry(read_json("llm-provider", "list", "--org", ORG, "--json"))
    if PROVIDER not in registry:
        print(f"BLOCKED: expected organization-level provider handle {PROVIDER!r} absent from amctl list")
        return 2
    failures = 0
    for agent in args.agents:
        base = f"/orgs/{ORG}/projects/{PROJECT}/agents/{agent}/model-configs"
        collection = get(base)
        summaries = parse_collection(collection)
        details = {s["uuid"]: get(f"{base}/{s['uuid']}") for s in summaries if s["type"] == "llm"}
        finding = classify_binding(collection, details, PROVIDER, "default", registry[PROVIDER])
        print(f"{agent}: {finding.code.upper()} :: {finding.explanation}" + (f" [binding_uuid={finding.uuid}]" if finding.uuid else ""))
        if not finding.healthy:
            failures += 1
    if failures:
        print(f"READ-ONLY AUDIT: {failures}/{len(args.agents)} agents require reconciliation/review; no modifications made")
        return 2
    print(f"READ-ONLY AUDIT: all {len(args.agents)} checked LLM bindings are associated (runtime health not tested)")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ContractError, OSError) as exc:
        print(f"LLM CONTRACT ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2)
