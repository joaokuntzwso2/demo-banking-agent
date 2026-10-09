"""Upstream Banking MCP app. Deploy outside the Agent Manager agent runtime."""
import os
import uvicorn
from mcp_server.app import app
if __name__=='__main__':
    uvicorn.run(app,host='0.0.0.0',port=int(os.getenv('PORT','8101')))
