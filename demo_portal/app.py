import os,hmac,httpx
from fastapi import FastAPI,Header,HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from pathlib import Path
from banking_common.config import CORE_URL,INTERNAL_KEY

app=FastAPI(title='Banco Aurora Portal')
app.mount('/static',StaticFiles(directory=Path(__file__).parent/'static'),name='static')
CONCIERGE=os.getenv('CONCIERGE_URL','http://localhost:8200')
TOKEN=os.getenv('DEMO_SESSION_TOKEN','local-demo-session')
CONCIERGE_API_KEY=os.getenv('CONCIERGE_API_KEY','')

def _concierge_headers():
    if os.getenv('WSO2_MODE','false').lower()=='true' and not CONCIERGE_API_KEY:
        raise HTTPException(503, 'Concierge gateway API key is not configured')
    h={'X-Internal-Key':INTERNAL_KEY}
    if CONCIERGE_API_KEY:h['X-API-Key']=CONCIERGE_API_KEY
    return h
def guard(token):
    if not hmac.compare_digest(token or '',TOKEN):raise HTTPException(401,'Demo session required')
async def core(method,path,payload=None):
    async with httpx.AsyncClient(timeout=30) as c:
        response=await c.request(method,CORE_URL+path,headers={'X-Internal-Key':INTERNAL_KEY},json=payload)
        if response.status_code>=400:raise HTTPException(response.status_code,response.text)
        return response.json()
@app.get('/',response_class=HTMLResponse)
def index():return (Path(__file__).parent/'static'/'index.html').read_text()
@app.get('/api/state')
async def state(x_demo_session:str|None=Header(None)):
    guard(x_demo_session);return await core('GET','/state/C001')
class Chat(BaseModel):message:str;language:str='en';history:list[dict]=[]
@app.post('/api/chat')
async def chat(r:Chat,x_demo_session:str|None=Header(None)):
    guard(x_demo_session)
    async with httpx.AsyncClient(timeout=90) as c:
        response=await c.post(CONCIERGE.rstrip('/')+'/chat',headers=_concierge_headers(),json={**r.model_dump(),'customer_id':'C001'})
        if response.status_code>=400:raise HTTPException(response.status_code,response.text)
        return response.json()
class Decision(BaseModel):
    approved:bool
    language:str='en'
    original_request:str=''
@app.post('/api/approvals/{approval_id}/decide')
async def decide(approval_id:str,r:Decision,x_demo_session:str|None=Header(None)):
    guard(x_demo_session)
    # One logical entry point: delegate the approval AND the actual execution
    # to Aurora Concierge. No specialist URLs or mutation tools in the BFF.
    async with httpx.AsyncClient(timeout=120) as c:
        response=await c.post(CONCIERGE.rstrip('/')+'/approve',
            headers=_concierge_headers(),json={
                'approval_id':approval_id,'approved':r.approved,
                'customer_id':'C001','language':r.language,
                'original_request':r.original_request})
        if response.status_code>=400:
            raise HTTPException(response.status_code,response.text)
        return response.json()

@app.post('/api/reset')
async def reset(x_demo_session:str|None=Header(None)):
    guard(x_demo_session)
    return await core('POST','/admin/reset')
