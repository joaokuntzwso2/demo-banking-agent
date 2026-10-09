"""WSO2 Agent Manager Platform-Hosted Custom API Agent: aurora-wellness."""
import os
os.environ["AGENT_ROLE"] = "wellness"
import uvicorn
from agents.specialists import app

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
