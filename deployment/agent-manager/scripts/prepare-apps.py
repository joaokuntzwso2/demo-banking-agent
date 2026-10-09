#!/usr/bin/env python3
"""Create self-contained, platform-hosted Python Git app paths for AMP.

Agent Manager builds each selected Git subdirectory independently; root-relative
imports are not reliable. Regenerate after every change to source agents.
"""
from pathlib import Path
import shutil

root=Path(__file__).resolve().parents[3]
apps=root/'deployment'/'agent-manager'/'apps'
roles=('concierge','account','card','dispute','pix','wellness')
for role in roles:
    dest=apps/('aurora-'+role)
    dest.mkdir(parents=True,exist_ok=True)
    for package in ('agents','banking_common','knowledge'):
        target=dest/package
        if target.exists(): shutil.rmtree(target)
        shutil.copytree(root/package,target,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    shutil.copy2(root/'requirements.txt',dest/'requirements.txt')
    module='agents.concierge' if role=='concierge' else 'agents.specialists'
    (dest/'main.py').write_text(f'''"""WSO2 Agent Manager Platform-Hosted Custom API Agent: aurora-{role}."""\nimport os\nos.environ["AGENT_ROLE"] = "{role}"\nimport uvicorn\nfrom {module} import app\n\nif __name__ == "__main__":\n    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8000")))\n''')
    (dest/'README.md').write_text(f'''# aurora-{role}\n\nSelf-contained Git build context for WSO2 Agent Manager 1.0.0.\n\n- **Source type:** Platform-Hosted → Source Code\n- **Project Path:** `/deployment/agent-manager/apps/aurora-{role}`\n- **Runtime:** Python 3.12; start command `python main.py`\n- **Interface:** Custom API Agent on port **8000**, OpenAPI path `/openapi.json`\n- **Endpoints:** `POST /chat`, GET `/health`{', POST `/approve`, GET `/workflow/{workflow_id}`' if role=='concierge' else ', POST `/execute` (write specialists only)'}\n- Read root README for environment variables, MCP Proxy, identity, gateway keys, and workflow.\n\n**Generated source.** Change the canonical modules at repository root and rerun\n`python deployment/agent-manager/scripts/prepare-apps.py`; commit the regenerated files before building agents.\n''')
    print(role,':', dest)
# standalone MCP upstream source bundle: not a platform-hosted agent
up=root/'deployment'/'agent-manager'/'upstream-mcp'
up.mkdir(parents=True,exist_ok=True)
for name in ('mcp_server','banking_common'):
    target=up/name
    if target.exists(): shutil.rmtree(target)
    shutil.copytree(root/name,target,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
shutil.copy2(root/'requirements.txt',up/'requirements.txt')
(up/'main.py').write_text('''"""Upstream Banking MCP app. Deploy outside the Agent Manager agent runtime."""\nimport os\nimport uvicorn\nfrom mcp_server.app import app\nif __name__=='__main__':\n    uvicorn.run(app,host='0.0.0.0',port=int(os.getenv('PORT','8101')))\n''')
(up/'Dockerfile').write_text('''FROM python:3.12-slim\nWORKDIR /app\nCOPY requirements.txt ./\nRUN pip install --no-cache-dir -r requirements.txt\nCOPY . .\nENV PYTHONUNBUFFERED=1 PORT=8101\nEXPOSE 8101\nCMD ["python", "main.py"]\n''')
(up/'README.md').write_text('''# Banking MCP upstream\n\nDeploy as a **normal Kubernetes/VM application**, not as one of the six Platform-Hosted agents. WSO2 Agent Manager registers its HTTPS `/mcp` endpoint as an MCP Proxy and exposes a governed environment-specific URL.\n\nDocker build from this folder: `docker build -t aurora-banking-mcp:1.0 .`\nConfigure `BANK_CORE_URL`, `BANK_CORE_READ_KEY`, `BANK_CORE_WRITE_KEY`, `MCP_ALLOWED_HOSTS`, `MCP_ALLOWED_ORIGINS`. Restrict ingress so only the WSO2 gateway can reach the upstream service. No gateway tokens belong in this upstream process. Read root README for full instructions.\n''')
print('MCP upstream:',up)
