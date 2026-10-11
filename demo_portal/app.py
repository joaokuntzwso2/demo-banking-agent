"""Banking backend-for-frontend.

Browser -> authenticated same-origin BFF -> Concierge (single agent entry).
No internal service credential or Agent Manager key is sent to the browser.
Synthetic single-customer demo only: NOT a production bank IAM integration.
"""
import os,hmac,hashlib,secrets,time,base64,uuid,httpx
from pathlib import Path
from fastapi import FastAPI,HTTPException,Request,Response,Depends
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel,Field
from banking_common.config import CORE_URL
from banking_common.security import secret
from banking_common.telemetry import install_telemetry,outbound_headers

app=FastAPI(title='Banco Aurora Demo Portal',version='1.1.0')
install_telemetry(app,'banking-portal')
app.mount('/static',StaticFiles(directory=Path(__file__).parent/'static'),name='static')
CONCIERGE=os.getenv('CONCIERGE_URL','http://localhost:8200')
COOKIE='aurora_session'
MAX_AGE=3600
LOGIN_FAILURES={}

def signing_key():
    key=os.getenv('DEMO_SESSION_SIGNING_KEY','')
    if not key and os.getenv('WSO2_MODE','false').lower()=='true':
        raise RuntimeError('DEMO_SESSION_SIGNING_KEY must be set in managed mode')
    return key or 'local-development-cookie-key-not-for-public-deployment'
def access_code():
    code=os.getenv('DEMO_ACCESS_CODE','')
    if not code and os.getenv('WSO2_MODE','false').lower()=='true':
        raise RuntimeError('DEMO_ACCESS_CODE must be configured in managed mode')
    return code or 'aurora-local-demo-only'
def digest(data):return hmac.new(signing_key().encode(),data.encode(),hashlib.sha256).hexdigest()
def encode_session(session_id):
    raw=f'{session_id}.{int(time.time())+MAX_AGE}'
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip('=')+'.'+digest(raw)
def decode_session(token):
    try:
        payload,signature=token.rsplit('.',1)
        raw=base64.urlsafe_b64decode(payload+'='*(-len(payload)%4)).decode()
        if not hmac.compare_digest(signature,digest(raw)):return None
        sid,expiry=raw.rsplit('.',1)
        if int(expiry)<time.time() or len(sid)!=32:return None
        return sid
    except Exception:return None  # malformed/untrusted cookie is an auth failure

def session(request:Request):
    sid=decode_session(request.cookies.get(COOKIE,''))
    if not sid:raise HTTPException(401,'Please sign in to the fictional banking demo')
    if request.method not in ('GET','HEAD'):
        expected=digest('csrf:'+sid)
        if not hmac.compare_digest(request.headers.get('X-CSRF-Token',''),expected):
            raise HTTPException(403,'Invalid CSRF token')
        origin=request.headers.get('origin')
        public_origin=os.getenv('DEMO_PUBLIC_ORIGIN','') or str(request.base_url).rstrip('/')
        if origin and origin!=public_origin:
            raise HTTPException(403,'Cross-origin request denied')
    return sid

def workflow_id(sid):return digest('workflow:'+sid)[:32]
def gateway_headers(approval=False):
    key=os.getenv('CONCIERGE_API_KEY','')
    if os.getenv('WSO2_MODE','false').lower()=='true' and not key:
        raise HTTPException(503,'Concierge Agent Manager invoke credential not configured')
    headers={'X-Agent-Call-Key':secret('CONCIERGE_CALL_KEY'),**outbound_headers()}
    if key:headers['X-API-Key']=key
    if approval:headers['X-Internal-Key']=secret('CONCIERGE_APPROVAL_KEY')
    return headers
async def core(method,path,payload=None,capability='BANK_CORE_READ_KEY'):
    async with httpx.AsyncClient(timeout=30) as client:
        res=await client.request(method,CORE_URL+path,json=payload,
            headers={'X-Internal-Key':secret(capability),**outbound_headers()})
        if res.status_code>=400:raise HTTPException(res.status_code,'Banking data unavailable')
        return res.json()
def concierge_transport():
    """Use AMP's Kubernetes gateway with the published virtual host.

    Applies only to the known Banco Aurora local k3d AMP environment. The
    browser never sees these addresses or any upstream API keys.
    """
    from urllib.parse import urlsplit
    url = CONCIERGE.rstrip('/')
    parsed = urlsplit(url)
    if not (os.getenv('WSO2_MODE', '').lower() == 'true'
            and os.getenv('KUBERNETES_SERVICE_HOST')):
        return url, {}
    if parsed.hostname == 'default-default.am-gateway.localhost':
        if (parsed.scheme != 'http' or parsed.port != 19080
                or parsed.path.rstrip('/') != '/aurora-concierge'
                or parsed.username or parsed.password or parsed.query or parsed.fragment):
            raise RuntimeError('Unrecognized local AMP Concierge gateway endpoint')
        return ('http://gateway-default.openchoreo-data-plane.svc.cluster.local:19080'
                + parsed.path.rstrip('/'), {'Host': parsed.netloc})
    return url, {}

async def agent(method,path,payload=None,approval=False):
    base_url, extra_headers = concierge_transport()
    async with httpx.AsyncClient(timeout=90) as client:
        res=await client.request(method,base_url+path,json=payload,
                                 headers={**gateway_headers(approval), **extra_headers})
        if res.status_code>=400:raise HTTPException(res.status_code,'Banking assistant is temporarily unavailable; retry safely')
        return res.json()

@app.get('/',response_class=HTMLResponse)
def index():return (Path(__file__).parent/'static'/'index.html').read_text()
class Login(BaseModel):access_code:str
@app.post('/api/login')
def login(payload:Login,response:Response,request:Request):
    # Demo-grade limiter. Use upstream rate limiting and real OIDC for any
    # internet-facing / production-facing deployment.
    ip=request.client.host if request.client else 'unknown'
    recent=[t for t in LOGIN_FAILURES.get(ip,[]) if time.monotonic()-t<60]
    if len(recent)>=5:raise HTTPException(429,'Too many login attempts')
    if not hmac.compare_digest(payload.access_code,access_code()):
        LOGIN_FAILURES[ip]=recent+[time.monotonic()]
        raise HTTPException(401,'Incorrect demo access code')
    LOGIN_FAILURES.pop(ip,None)
    sid=uuid.uuid4().hex
    response.set_cookie(COOKIE,encode_session(sid),httponly=True,samesite='strict',
                        secure=os.getenv('DEMO_SECURE_COOKIE','false').lower()=='true',max_age=MAX_AGE,path='/')
    return {'authenticated':True,'csrf_token':digest('csrf:'+sid),'expires_in':MAX_AGE}
@app.get('/api/session')
def get_session(sid:str=Depends(session)):
    return {'authenticated':True,'csrf_token':digest('csrf:'+sid),'expires_in':MAX_AGE}
@app.post('/api/logout')
def logout(response:Response,sid:str=Depends(session)):
    response.delete_cookie(COOKIE,path='/');return {'authenticated':False}
@app.get('/api/state')
async def state(sid:str=Depends(session)):
    return await core('GET','/state/C001')
class Chat(BaseModel):
    message:str=Field(min_length=1,max_length=3000)
    language:str='en'
    history:list[dict]=Field(default_factory=list,max_length=12)
@app.post('/api/chat')
async def chat(req:Chat,sid:str=Depends(session)):
    return await agent('POST','/chat',{**req.model_dump(),'customer_id':'C001','workflow_id':workflow_id(sid)})
class Decision(BaseModel):approved:bool;language:str='en';original_request:str=''
@app.post('/api/approvals/{approval_id}/decide')
async def decide(approval_id:str,req:Decision,sid:str=Depends(session)):
    return await agent('POST','/approve',{'approval_id':approval_id,'approved':req.approved,
       'customer_id':'C001','workflow_id':workflow_id(sid),'language':req.language,
       'original_request':req.original_request},approval=True)
class OperatorDecision(BaseModel):
    status:str
    operator_code:str
@app.post('/api/operator/cases/{case_id}/transition')
async def operator_transition(case_id:str,req:OperatorDecision,sid:str=Depends(session)):
    expected=os.getenv('DEMO_OPERATOR_CODE','')
    if not expected:raise HTTPException(403,'Presenter operator console is disabled')
    if not hmac.compare_digest(req.operator_code,expected):raise HTTPException(403,'Incorrect operator code')
    if req.status not in ('assigned','in_progress','resolved'):raise HTTPException(422,'Invalid status')
    # Restricted separately from the customer-facing Concierge.
    return await core('POST',f'/service-cases/{case_id}/assign',
        {'customer_id':'C001','status':req.status},capability='BANK_CASE_OPERATOR_KEY')

@app.get('/api/workflow')
async def pending(sid:str=Depends(session)):
    return await agent('GET','/workflow/'+workflow_id(sid)+'?customer_id=C001',approval=True)
class Reset(BaseModel):reset_code:str
@app.post('/api/reset')
async def reset(req:Reset,sid:str=Depends(session)):
    expected=os.getenv('DEMO_RESET_CODE','')
    if not expected:raise HTTPException(403,'Presenter reset disabled; set DEMO_RESET_CODE')
    if not hmac.compare_digest(req.reset_code,expected):raise HTTPException(403,'Incorrect presenter reset code')
    return await core('POST','/admin/reset',capability='BANK_RESET_KEY')
