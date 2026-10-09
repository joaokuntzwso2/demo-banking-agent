"""WSO2 Agent Manager Platform-Hosted Custom API Agent: aurora-concierge."""
import os
os.environ["AGENT_ROLE"] = "concierge"
import uvicorn
from agents.concierge import app

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
