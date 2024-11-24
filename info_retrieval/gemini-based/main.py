import asyncio
from agent import AWSAgent

if __name__ == "__main__":
    agent = AWSAgent(pathtodata="/Users/rkala/Documents/GitHub/iac_agent/info_retrieval/data")
    asyncio.run(agent.run())
