"""Distinct service-to-service capabilities. Legacy key fallback is LOCAL DEMO ONLY."""
import os,hmac
from fastapi import HTTPException

def secret(env):
    value=os.getenv(env,'')
    if value:return value
    if os.getenv('WSO2_MODE','false').lower()=='true':
        raise RuntimeError(f'{env} must be configured for a managed environment')
    return os.getenv('BANK_INTERNAL_KEY','local-demo-internal-key')

def require_secret(provided,env):
    if not hmac.compare_digest(provided or '',secret(env)):
        raise HTTPException(401,'Service authentication required')
