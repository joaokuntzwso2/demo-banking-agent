"""WSO2 Agent Manager Platform-Hosted Custom API Agent: aurora-account."""
import os
os.environ["AGENT_ROLE"] = "account"
import uvicorn
from agents.specialists import app

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
