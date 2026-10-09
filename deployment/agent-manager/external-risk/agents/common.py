import os, json, httpx, asyncio, time
from pydantic import BaseModel,Field
from fastapi import FastAPI,HTTPException,Header
from banking_common.config import MCP_URL,MCP_TOKEN
from banking_common.telemetry import install_telemetry,outbound_headers
from banking_common.security import require_secret
from mcp.client.streamable_http import streamable_http_client
from mcp import ClientSession
from pathlib import Path

class ChatRequest(BaseModel):
    message:str;customer_id:str='C001';language:str='en';history:list[dict]=Field(default_factory=list);workflow_id:str|None=None
class AgentResponse(BaseModel):
    agent:str;message:str;data:dict=Field(default_factory=dict);next_actions:list[dict]=Field(default_factory=list)

def choose_language(message,provided='en'):
    s=message.lower()
    es=['hola','necesito','tarjeta','saldo','cuenta','pago','desconozco','puedes','gracias','transferencia','qué','cuánto','bloquear','reclamo']
    en=['hello','please','card','balance','account','payment','dispute','can you','help','transfer','what','how much','block']
    a=sum(w in s for w in es);b=sum(w in s for w in en)
    return 'es' if a>b else 'en' if b>a else provided

# Defense in depth: Agent Manager MCP OAuth tool permissions must ALSO be configured.
TOOLS_BY_ROLE = {
    'account': {'get_account_balance','list_account_transactions','get_dispute_status','list_beneficiaries'},
    'card': {'get_card_status','request_card_block','execute_approved_card_block'},
    'dispute': {'list_card_transactions','get_dispute_status','get_human_cases',
                'request_dispute','execute_approved_dispute','request_human_escalation'},
    'pix': {'get_account_balance','list_beneficiaries','request_pix','execute_approved_pix'},
    'wellness': {'get_account_balance','get_upcoming_obligations'},
    'risk': {'list_beneficiaries'},
    'concierge': set(),
}
_token_cache = {}
_token_lock = asyncio.Lock()

async def _agentid_token(resource: str):
    client_id = os.getenv('AMP_AGENTID_CLIENT_ID')
    secret = os.getenv('AMP_AGENTID_CLIENT_SECRET')
    endpoint = os.getenv('AMP_AGENTID_TOKEN_ENDPOINT')
    if not (client_id and secret and endpoint):
        raise RuntimeError('AgentID not provisioned: check AMP_AGENTID_* injected variables')
    scopes = os.getenv('AMP_AGENTID_SCOPES', '')
    cache_key = (resource, scopes)
    async with _token_lock:
        found = _token_cache.get(cache_key)
        if found and found[1] > time.monotonic()+30:
            return found[0]
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(endpoint, auth=(client_id, secret),
                data={'grant_type':'client_credentials', 'scope':scopes,
                      'resource':resource})
            response.raise_for_status()
            result=response.json()
        token=result['access_token']
        _token_cache[cache_key]=(token,time.monotonic()+int(result.get('expires_in',3600)))
        return token

async def _mcp_headers():
    if os.getenv('WSO2_MODE','false').lower()=='true' and not os.getenv('MCP_SERVER_URL'):
        raise RuntimeError('Missing injected MCP_SERVER_URL for managed agent')
    mode = os.getenv('MCP_AUTH_MODE','local').lower()
    if mode == 'agentid':
        return {'Authorization': 'Bearer ' + await _agentid_token(MCP_URL)}
    if mode == 'apikey':
        key=os.getenv('MCP_PROXY_API_KEY','')
        if not key: raise RuntimeError('MCP_PROXY_API_KEY missing')
        return {os.getenv('MCP_API_KEY_HEADER', 'X-API-Key'):key}
    if mode == 'local' and os.getenv('WSO2_MODE','false').lower() != 'true':
        return {'Authorization': f'Bearer {MCP_TOKEN}'} if MCP_TOKEN else {}
    raise RuntimeError('MCP_AUTH_MODE must be agentid or apikey in WSO2_MODE')

async def mcp_tool(name,arguments):
    role=os.getenv('AGENT_ROLE')
    if role in TOOLS_BY_ROLE and name not in TOOLS_BY_ROLE[role]:
        raise PermissionError(f'Agent {role} may not invoke banking MCP tool {name}')
    # MCP Python SDK 1.25+ accepts a preconfigured httpx.AsyncClient here.
    # Do not pass headers= or timeout= to streamable_http_client(): those
    # arguments were removed from this function (the SDK still yields 3 items).
    headers = {**await _mcp_headers(), **outbound_headers()}
    timeout = httpx.Timeout(30.0, read=300.0)
    async with httpx.AsyncClient(headers=headers, timeout=timeout) as http_client:
        async with streamable_http_client(MCP_URL, http_client=http_client) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                response = await session.call_tool(name, arguments)
                if response.isError:
                    raise RuntimeError(str(response.content))
                if response.structuredContent:
                    data = response.structuredContent
                    return data.get('result', data) if isinstance(data, dict) else data
                for item in response.content:
                    if item.type == 'text':
                        try:
                            return json.loads(item.text)
                        except ValueError:
                            return {'text': item.text}
                return {}

async def llm(system,user):
    """Managed-mode LLM calls MUST route through the attached WSO2 provider.

    LLM_PROVIDER_URL and LLM_PROVIDER_KEY are injected by the Agent Manager
    LLM configuration. The OpenAI-compatible API key is a gateway-issued key,
    not an underlying provider secret. No direct-provider fallback is allowed
    in managed mode when USE_LLM_PROVIDER=true.
    """
    managed=os.getenv('USE_LLM_PROVIDER','false').lower()=='true'
    if managed:
        url=os.getenv('LLM_PROVIDER_URL','').strip()
        key=os.getenv('LLM_PROVIDER_KEY','').strip()
        if not url or not key:
            raise RuntimeError('Managed LLM binding missing: LLM_PROVIDER_URL/LLM_PROVIDER_KEY')
    else:
        if os.getenv('WSO2_MODE','false').lower()=='true' and os.getenv('REQUIRE_GOVERNED_LLM','true').lower()=='true':
            raise RuntimeError('Managed agents require USE_LLM_PROVIDER=true or explicit REQUIRE_GOVERNED_LLM=false')
        key=os.getenv('OPENAI_API_KEY','').strip()
        url=os.getenv('OPENAI_URL','').strip()
        if not key:return None
    from openai import AsyncOpenAI
    options={'api_key':key}
    if url:options['base_url']=url
    client=AsyncOpenAI(**options)
    result=await client.chat.completions.create(
        model=os.getenv('OPENAI_MODEL','gpt-4.1-mini'),temperature=0,
        messages=[{'role':'system','content':system},{'role':'user','content':user}])
    return result.choices[0].message.content

def retrieve(topic,lang,query=''):
    """Local document retrieval: filter corpus by topic/language, then rank by lexical term overlap.
    This is retrieval-grounding, not semantic vector RAG; see README for production upgrade.
    """
    import re
    docs=json.loads((Path(__file__).parent.parent/'knowledge'/'policies.json').read_text())
    candidates=[d for d in docs if d['topic']==topic and d['language']==('es' if lang=='es' else 'en')]
    tokens=set(re.findall(r"[\wÀ-ÿ]+",query.lower()))
    best=max(candidates,key=lambda d:len(tokens&set(re.findall(r"[\wÀ-ÿ]+",d['text'].lower()))))
    return best['text']+f" [{best['id']}, v{best['version']}]"

def application(name,fn):
    app=FastAPI(title='Aurora '+name+' Agent')
    install_telemetry(app,name)
    @app.get('/health')
    async def health():return {'status':'ok','agent':name,'agent_version':os.getenv('AGENT_VERSION','dev-local')}
    @app.post('/chat',response_model=AgentResponse)
    async def chat(req:ChatRequest,x_agent_call_key:str|None=Header(None)):
        require_secret(x_agent_call_key,'CONCIERGE_CALL_KEY' if name=='concierge' else 'SPECIALIST_CALL_KEY')
        if req.customer_id != 'C001':
            raise HTTPException(403,'This demo agent is bound to fictional customer C001')
        return await fn(req)
    return app
