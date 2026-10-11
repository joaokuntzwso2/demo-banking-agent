#!/usr/bin/env python3
"""Read-only proof that existing Concierge has the expected managed secret env vars."""
import argparse
import importlib.util
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parent.parent
SPEC=importlib.util.spec_from_file_location('aurora_reconciler_secret_verification',ROOT/'scripts/aurora-full-reconcile.py')
module=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)

if __name__=='__main__':
    parser=argparse.ArgumentParser(description='Read-only Concierge managed secret validation')
    parser.add_argument('--runtime', action='store_true',
                        help='Also verify the running container environment (read-only kubectl exec)')
    parser.add_argument('--strict-process', action='store_true',
                        help='Require in-process HMAC proof (fails on minimal images with no exec Python)')
    args=parser.parse_args()
    try:
        result=module.verify_concierge_managed_secrets(
            module.service_secrets(),
            require_runtime=args.runtime or args.strict_process,
            strict_process=args.strict_process)
        if args.runtime or args.strict_process:
            if result=='process-verified':
                print('VERIFIED: Concierge Kubernetes Secret references and running process environment match expected private credentials. No changes made.')
            else:
                print('PARTIALLY VERIFIED: Concierge Kubernetes Secret references and values match, and pod is Ready; in-process environment was NOT directly verified (minimal container). No changes made. Run Portal/gateway and E2E checks.')
        else:
            print('VERIFIED: Concierge managed Kubernetes Secret references and values match private expected credentials. Process environment not checked. No changes made.')
    except Exception as exc:
        print(f'NOT VERIFIED: {exc}',file=sys.stderr)
        sys.exit(1)
