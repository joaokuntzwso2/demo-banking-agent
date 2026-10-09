#!/usr/bin/env python3
"""Create a private local environment file with random demo service secrets."""
import argparse,secrets
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--output',default='.env.executive');args=p.parse_args()
root=Path(__file__).resolve().parents[1]
out=Path(args.output)
if out.exists():raise SystemExit(f'Existing {out} will not be overwritten.')
sensitive=['BANK_CORE_READ_KEY','BANK_CORE_WRITE_KEY','BANK_APPROVAL_KEY','SPECIALIST_EXECUTION_KEY',
 'CONCIERGE_APPROVAL_KEY','BANK_RESET_KEY','DEMO_ACCESS_CODE','DEMO_RESET_CODE',
 'DEMO_SESSION_SIGNING_KEY','CONCIERGE_CALL_KEY','SPECIALIST_CALL_KEY',
 'BANK_CASE_OPERATOR_KEY','DEMO_OPERATOR_CODE']
lines=[]
for line in (root/'.env.example').read_text().splitlines():
 if '=' in line and not line.strip().startswith('#'):
  key=line.split('=',1)[0]
  if key in sensitive:line=key+'='+secrets.token_hex(32)
 lines.append(line)
out.write_text('\n'.join(lines)+'\n')
out.chmod(0o600)
print(f'Created {out} (mode 0600). Credentials are not printed.')
print(f'Start local: docker compose --env-file {out} up -d --build')
print('Use the new DEMO_ACCESS_CODE from that private file to sign in.')
print('Keep the file out of Git and away from the browser.')
