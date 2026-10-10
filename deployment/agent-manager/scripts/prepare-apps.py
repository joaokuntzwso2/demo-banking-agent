#!/usr/bin/env python3
"""Create self-contained, platform-hosted Python Git app paths for AMP.

Agent Manager builds each selected Git subdirectory independently; root-relative
imports are not reliable. Regenerate after every change to source agents.
"""
from pathlib import Path
import shutil
import json


def openapi_spec(role):
    """Deterministic OpenAPI 3.0 contract for the actual FastAPI routes.

    Do not import the running agents here. Git source generation must work in a
    clean Python 3.x checkout, before any runtime dependencies are installed.
    """
    schemas = {
        'ChatRequest': {
            'type': 'object', 'required': ['message'],
            'properties': {
                'message': {'type': 'string'},
                'customer_id': {'type': 'string', 'default': 'C001'},
                'language': {'type': 'string', 'default': 'en'},
                'history': {'type': 'array', 'items': {'type': 'object', 'additionalProperties': True}, 'default': []},
                'workflow_id': {'type': 'string', 'nullable': True},
            },
        },
        'AgentResponse': {
            'type': 'object', 'required': ['agent', 'message'],
            'properties': {
                'agent': {'type': 'string'},
                'message': {'type': 'string'},
                'data': {'type': 'object', 'additionalProperties': True, 'default': {}},
                'next_actions': {'type': 'array', 'items': {'type': 'object', 'additionalProperties': True}, 'default': []},
            },
        },
        'ExecuteRequest': {
            'type': 'object', 'required': ['approval_id', 'payload'],
            'properties': {
                'customer_id': {'type': 'string', 'default': 'C001'},
                'approval_id': {'type': 'string'},
                'payload': {'type': 'object', 'additionalProperties': True},
            },
        },
        'ApprovalDecision': {
            'type': 'object', 'required': ['approval_id', 'approved', 'workflow_id'],
            'properties': {
                'approval_id': {'type': 'string'},
                'approved': {'type': 'boolean'},
                'customer_id': {'type': 'string', 'default': 'C001'},
                'workflow_id': {'type': 'string'},
                'language': {'type': 'string', 'default': 'en'},
                'original_request': {'type': 'string', 'default': ''},
            },
        },
    }

    def operation(method, summary, response_schema=None, request_schema=None, auth=None, parameters=None):
        obj = {
            'summary': summary,
            'operationId': f'aurora_{role}_{method}_{summary.lower().replace(" ", "_")}',
            'responses': {
                '200': {'description': 'Successful response', 'content': {'application/json': {'schema': response_schema or {'type': 'object', 'additionalProperties': True}}}},
                '403': {'description': 'Forbidden or unapproved operation'},
                '422': {'description': 'Request validation error'},
            },
        }
        if request_schema:
            obj['requestBody'] = {'required': True, 'content': {'application/json': {'schema': {'$ref': f'#/components/schemas/{request_schema}'}}}}
        if auth:
            obj['security'] = [{auth: []}]
            obj['responses']['401'] = {'description': 'Missing or incorrect internal application credential'}
        if parameters:
            obj['parameters'] = parameters
        return obj

    paths = {
        '/health': {'get': operation('get', 'Health')},
        '/chat': {'post': operation('post', 'Chat', {'$ref': '#/components/schemas/AgentResponse'}, 'ChatRequest', 'AgentCallKey')},
    }
    if role == 'concierge':
        paths['/approve'] = {'post': operation('post', 'Approve', request_schema='ApprovalDecision', auth='InternalKey')}
        paths['/workflow/{workflow_id}'] = {'get': operation('get', 'Get workflow', auth='InternalKey', parameters=[
            {'name': 'workflow_id', 'in': 'path', 'required': True, 'schema': {'type': 'string'}},
            {'name': 'customer_id', 'in': 'query', 'required': False, 'schema': {'type': 'string', 'default': 'C001'}},
        ])}
    else:
        # All specialists expose /execute. Read-only specialists deliberately
        # return HTTP 403 if execution is attempted.
        paths['/execute'] = {'post': operation('post', 'Execute', request_schema='ExecuteRequest', auth='InternalKey')}
    return {
        'openapi': '3.0.3',
        'info': {'title': f'Aurora {role.title()} Agent', 'version': '1.0.0', 'description': 'Banco Aurora synthetic managed agent API'},
        'paths': paths,
        'components': {
            'schemas': schemas,
            'securitySchemes': {
                'AgentCallKey': {'type': 'apiKey', 'in': 'header', 'name': 'X-Agent-Call-Key'},
                'InternalKey': {'type': 'apiKey', 'in': 'header', 'name': 'X-Internal-Key'},
            },
        },
    }


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
    (dest/'openapi.json').write_text(json.dumps(openapi_spec(role), indent=2, sort_keys=True) + '\n')
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

# Optional seventh externally hosted risk advisor, built and run outside AMP.
# It is registered in Agent Manager as an External Agent by the presenter.
ext=root/'deployment'/'agent-manager'/'external-risk'
ext.mkdir(parents=True,exist_ok=True)
for name in ('agents','banking_common','knowledge'):
    target=ext/name
    if target.exists():shutil.rmtree(target)
    shutil.copytree(root/name,target,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
shutil.copy2(root/'requirements.txt',ext/'requirements.txt')
(ext/'main.py').write_text("""import os,uvicorn
os.environ['AGENT_ROLE']='risk'
from agents.risk import app
if __name__=='__main__':
    uvicorn.run(app,host='0.0.0.0',port=int(os.getenv('PORT','8000')))
""")
(ext/'Dockerfile').write_text("""FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV PORT=8000
EXPOSE 8000
CMD ["python","main.py"]
""")
(ext/'README.md').write_text("""# Optional external risk agent

Run as a normal container/VM service, then register its HTTPS `/chat` endpoint in
WSO2 Agent Manager as **Externally-Hosted**. Requires governed MCP Proxy
`MCP_SERVER_URL` + `MCP_AUTH_MODE=apikey` and `MCP_PROXY_API_KEY`, or external
AgentID client credentials. Grant **only `list_beneficiaries`** tool scope.
Configure `AGENT_RISK_URL` and `AGENT_RISK_API_KEY` in Concierge. All decisions
are advisory; Pix and the Banking Core enforce recipient verification.
""")
print('Optional external risk agent:',ext)
