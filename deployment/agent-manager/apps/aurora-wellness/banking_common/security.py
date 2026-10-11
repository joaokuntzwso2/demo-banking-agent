"""Distinct service-to-service capabilities. Legacy key fallback is LOCAL DEMO ONLY."""
import os,hmac
from fastapi import HTTPException

# Public demonstration credentials must never authenticate a managed runtime.
_PUBLIC_DEMO_KEYS = frozenset({"local-demo-internal-key", "local-demo-mcp-secret"})


def _explicit_local_rehearsal():
    """Legacy demo credentials require explicit opt-in outside Kubernetes/WSO2."""
    return (
        os.getenv("AURORA_ALLOW_PUBLIC_LOCAL_DEMO_KEYS") == "local-only"
        and os.getenv("WSO2_MODE", "false").lower() != "true"
        and not os.getenv("KUBERNETES_SERVICE_HOST")
        and not os.path.exists("/var/run/secrets/kubernetes.io/serviceaccount")
    )


def secret(env):
    value = os.getenv(env, "")
    if value:
        if value in _PUBLIC_DEMO_KEYS and not _explicit_local_rehearsal():
            raise RuntimeError(f"{env} is configured with an insecure public demo value")
        return value
    if _explicit_local_rehearsal():
        # Legacy local-only tests and Compose rehearsal. NEVER enabled by default.
        return os.getenv("BANK_INTERNAL_KEY") or "local-demo-internal-key"
    raise RuntimeError(f"{env} must be configured; implicit demo-key fallback is disabled")


def require_secret(provided,env):
    if not hmac.compare_digest(provided or '',secret(env)):
        raise HTTPException(401,'Service authentication required')
