import os

def env(key, default=""):
    return os.getenv(key, default)

CORE_URL = env("BANK_CORE_URL", "http://localhost:8100")
INTERNAL_KEY = env("BANK_INTERNAL_KEY")
MCP_URL = env("MCP_SERVER_URL", "http://localhost:8101/mcp")
MCP_TOKEN = env("MCP_BEARER_TOKEN")
