import os
from datetime import datetime
from langchain_openai import ChatOpenAI
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

llm = ChatOpenAI(model="gpt-4o", temperature=0, api_key=os.getenv("OPENAI_API_KEY"))

def append_state_update(repo_path: str, agent_name: str, current_action: str):
    """
    Append a status update to the agent's state file using LLM to generate the message.
    
    Args:
        repo_path: Path to repository
        agent_name: Name of the agent (e.g., 'planning', 'execution')
        current_action: Description of current action
    """
    try:
        # Generate concise status message
        prompt = f"""
        Generate a very concise one-line status update (max 50 chars) for this action:
        Agent: {agent_name}
        Action: {current_action}
        
        Format: Present tense, active voice, no fluff.
        Example: "Analyzing file structure"
        """
        
        status = llm.invoke(prompt).content.strip()
        
        # Create state file path
        state_file = os.path.join(repo_path, f"{agent_name}_state.txt")
        
        # Append update with timestamp
        with open(state_file, 'a') as f:
            f.write(f"[{datetime.now().strftime('%H:%M:%S')}] {status}\n")
            
    except Exception:
        pass  # Silently handle errors 