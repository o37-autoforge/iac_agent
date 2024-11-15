# main.py

import asyncio
from forge_agent_v0.agent import forgeAgent
 

def main():
    agent = forgeAgent()
    asyncio.run(agent.handle_user_interaction())

if __name__ == "__main__":
    main()