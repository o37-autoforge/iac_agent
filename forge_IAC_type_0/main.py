# main.py

import asyncio
from forge_agent_v0.agent import forgeAgent
import warnings
from langchain_core.globals import set_verbose, set_debug

# Disable verbose logging
set_verbose(False)

# Disable debug logging
set_debug(False)

# Suppress all warnings
warnings.filterwarnings("ignore")
warnings.filterwarnings("ignore", module="langchain")
warnings.filterwarnings("ignore", category=DeprecationWarning)

import logging

logging.getLogger().setLevel(logging.CRITICAL)

def main():
    agent = forgeAgent()
    asyncio.run(agent.handle_user_interaction())

if __name__ == "__main__":
    main()