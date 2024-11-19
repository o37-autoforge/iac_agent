# main.py

import asyncio
import logging
import sys
import traceback
from forge_agent_v1.agent import forgeAgent

def main():
    agent = forgeAgent(applyChanges = False, autoPR = False)
    asyncio.run(agent.handle_user_interaction())

if __name__ == "__main__":
    main()