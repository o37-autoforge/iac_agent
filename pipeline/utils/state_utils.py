import os
from datetime import datetime
from langchain_openai import ChatOpenAI
from pathlib import Path
from dotenv import load_dotenv
import logging

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

llm = ChatOpenAI(model="gpt-4o", temperature=0, api_key=os.getenv("OPENAI_API_KEY"))

def append_state_update(repo_path: str, agent_name: str, node_name: str, current_action: str):
    """
    Append a status update to the agent's state file using LLM to generate the message.
    
    Args:
        repo_path: Path to repository
        agent_name: Name of the agent (e.g., 'planning', 'execution')
        node_name: Name of the current node in the workflow
        current_action: Description of current action
    """
    try:
        # Generate concise status message
        prompt = f"""
        Generate a very concise one-line status update (max 50 chars) for this action:
        Agent: {agent_name}
        Node: {node_name}
        Action: {current_action}
        
        Format: Present tense, active voice, no fluff.
        Example: "Analyzing file structure in planning node"
        """
        
        status = llm.invoke(prompt).content.strip()
        
        # Create state directory if it doesn't exist
        state_dir = os.path.join(repo_path, "agent_states")
        os.makedirs(state_dir, exist_ok=True)
        
        # Create state file path with agent and node info
        state_file = os.path.join(state_dir, f"{agent_name}_state.txt")
        
        # Append update with timestamp and node info
        with open(state_file, 'a') as f:
            f.write(f"[{datetime.now().strftime('%H:%M:%S')}] [{node_name}] {status}\n")
            
    except Exception as e:
        logger.error(f"Error appending state update: {str(e)}")
        # Still fail silently but log the error