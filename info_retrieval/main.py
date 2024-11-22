# main.py

import asyncio
from agent import InfoRetrievalAgent

def main():
    agent = InfoRetrievalAgent()
    asyncio.run(agent.handle_query())

if __name__ == "__main__":
    main()  