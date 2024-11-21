# main.py

import asyncio
from forge_agent_v1_1.agent import forgeAgent

def main():
    agent = forgeAgent(applyChanges = True, autoPR = True)
    asyncio.run(agent.handle_query())

if __name__ == "__main__":
    main()  