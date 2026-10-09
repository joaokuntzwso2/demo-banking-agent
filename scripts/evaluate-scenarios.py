#!/usr/bin/env python3
"""Deterministic scorecard for a real scenario report. NOT WSO2 evaluations.

Use AMP evaluation monitors in the console for actual trace-based scores.
"""
import argparse,json
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('report',nargs='?',default='executive-scenario-report.json');p.add_argument('--simulate-drift',action='store_true');args=p.parse_args()
d=json.loads(Path(args.report).read_text());measured=[x for x in d['scenarios'] if x['passed'] is not None]
if not measured:raise SystemExit('Report contains no measured scenarios')
passes=sum(bool(x['passed']) for x in measured)
print(f'LOCAL SYNTHETIC SCENARIO SCORECARD: {passes}/{len(measured)} passed')
for x in measured:print(('PASS' if x['passed'] else 'FAIL'),x['scenario'],x['evidence'])
print('S07/S08 are NOT scored until verified in actual WSO2 Agent Manager.')
checks=d.get('quality_checks',{})
for name,status in checks.items():
    print(('PASS' if status else 'FAIL')+' LOCAL_RULE '+name)
if args.simulate_drift:
    # A controlled local-only fault, never described as an AMP monitor result.
    degraded=dict(checks,language_fidelity=False)
    print('ILLUSTRATIVE LOCAL DRIFT INJECTION — NOT A PLATFORM TRACE:')
    for name,status in degraded.items():
        if not status:print('FAIL LOCAL_RULE '+name+' (injected)')
    print('Expected real-product follow-up: configure a Future Traces monitor in AMP, generate an actual Spanish regression, and inspect its scored trace.')
if passes!=len(measured) or not all(checks.values()):raise SystemExit(1)
