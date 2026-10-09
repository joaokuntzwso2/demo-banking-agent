"""Upstream MCP server; publish /mcp via Agent Manager MCP Proxy."""
import os,httpx
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from banking_common.config import CORE_URL
from banking_common.security import secret
from banking_common.telemetry import outbound_headers,wrap_asgi
# MCP Python SDK protects against DNS rebinding. Explicitly allow the Docker
# Compose service name so that http://mcp:8101/mcp is not rejected with HTTP 421.
# On-prem: set MCP_ALLOWED_HOSTS to the actual internal upstream host(s).
def allowed_hosts_env(name, default):
    return [host.strip() for host in os.getenv(name, default).split(',') if host.strip()]

mcp=FastMCP(
    'aurora-banking-tools',
    stateless_http=True,
    json_response=True,
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=allowed_hosts_env('MCP_ALLOWED_HOSTS', 'mcp:*,localhost:*,127.0.0.1:*'),
        allowed_origins=allowed_hosts_env('MCP_ALLOWED_ORIGINS', 'http://localhost:*,http://127.0.0.1:*'),
    ),
)
async def core(method,path,payload=None):
    async with httpx.AsyncClient(timeout=20) as c:
        r=await c.request(method,CORE_URL+path,headers={'X-Internal-Key':secret('BANK_CORE_READ_KEY' if method=='GET' else 'BANK_CORE_WRITE_KEY'),**outbound_headers()},json=payload)
        r.raise_for_status();return r.json()
# Granular read capabilities minimize exposed banking data per specialist.
@mcp.tool()
async def get_account_balance(customer_id:str)->dict:
    """Read checking account balance only, for authorized customer."""
    snapshot=await core('GET','/state/'+customer_id)
    return {'account':snapshot['account'],'customer':{'id':snapshot['customer']['id'],'name':snapshot['customer']['name']}}

@mcp.tool()
async def list_account_transactions(customer_id:str)->dict:
    """Read transaction history and Pix receipts without beneficiary identifiers."""
    snap=await core('GET','/state/'+customer_id)
    names={b['id']:b['name'] for b in snap['beneficiaries']}
    receipts=[{**p,'beneficiary_name':names.get(p['beneficiary_id'],'Verified beneficiary')} for p in snap['payments']]
    return {'transactions':snap['transactions'],'payments':receipts}

@mcp.tool()
async def get_card_status(customer_id:str)->dict:
    """List customer cards and security state."""
    return {'cards':(await core('GET','/state/'+customer_id))['cards']}

@mcp.tool()
async def list_card_transactions(customer_id:str)->dict:
    """Read card transactions with merchant/amount for a dispute."""
    s=await core('GET','/state/'+customer_id)
    return {'transactions':[t for t in s['transactions'] if t['kind']=='card']}

@mcp.tool()
async def get_dispute_status(customer_id:str)->dict:
    """Read existing card dispute case statuses."""
    return {'disputes':(await core('GET','/state/'+customer_id))['disputes']}

@mcp.tool()
async def list_beneficiaries(customer_id:str)->dict:
    """Read beneficiary IDs, names and verification status; never return Pix keys."""
    s=await core('GET','/state/'+customer_id)
    return {'beneficiaries':[{'id':b['id'],'name':b['name'],'verified':bool(b['verified'])} for b in s['beneficiaries']]}

@mcp.tool()
async def get_upcoming_obligations(customer_id:str)->dict:
    """Read scheduled obligations for an educational cash-flow calculation."""
    return {'obligations':(await core('GET','/state/'+customer_id))['obligations']}

@mcp.tool()
async def get_human_cases(customer_id:str)->dict:
    """Read human support queue cases for the customer."""
    return {'service_cases':(await core('GET','/state/'+customer_id))['service_cases']}

@mcp.tool()
async def request_human_escalation(customer_id:str,reason:str,priority:str='normal')->dict:
    """Open a human service case only upon explicit customer request, no financial mutation."""
    return await core('POST','/service-cases',{'customer_id':customer_id,'reason':reason,'priority':priority})

@mcp.tool()
async def request_card_block(customer_id:str,card_id:str)->dict:
    """Create pending human approval; DOES NOT block a card."""
    return await core('POST','/approvals',{'customer_id':customer_id,'action':'block_card','payload':{'card_id':card_id}})
@mcp.tool()
async def execute_approved_card_block(customer_id:str,card_id:str,approval_id:str)->dict:
    """Block card only if a matching approval has already been granted."""
    return await core('POST','/cards/block',{'customer_id':customer_id,'card_id':card_id,'approval_id':approval_id})
@mcp.tool()
async def request_dispute(customer_id:str,transaction_id:str)->dict:
    """Create pending human approval to open card dispute; no refund guaranteed."""
    return await core('POST','/approvals',{'customer_id':customer_id,'action':'open_dispute','payload':{'transaction_id':transaction_id}})
@mcp.tool()
async def execute_approved_dispute(customer_id:str,transaction_id:str,approval_id:str)->dict:
    """Open dispute case after matching human approval."""
    return await core('POST','/disputes',{'customer_id':customer_id,'transaction_id':transaction_id,'approval_id':approval_id})
@mcp.tool()
async def request_pix(customer_id:str,beneficiary_id:str,amount:str)->dict:
    """Create a pending approval for Pix; never transfer funds in this step."""
    return await core('POST','/approvals',{'customer_id':customer_id,'action':'execute_pix','payload':{'beneficiary_id':beneficiary_id,'amount':amount}})
@mcp.tool()
async def execute_approved_pix(customer_id:str,beneficiary_id:str,amount:str,approval_id:str,idempotency_key:str)->dict:
    """Execute simulated Pix exactly once after approved immutable details."""
    return await core('POST','/pix',{'customer_id':customer_id,'beneficiary_id':beneficiary_id,'amount':amount,'approval_id':approval_id,'idempotency_key':idempotency_key})
app=wrap_asgi(mcp.streamable_http_app())
