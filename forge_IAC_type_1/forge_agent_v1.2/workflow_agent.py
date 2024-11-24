from langgraph.graph import StateGraph, END
from utils.git_utils import clone_repository, create_combined_txt
from utils.workflow_utils import execute_parallel_tasks, configure_aws_from_env
from typing import TypedDict, List, Union, Optional
from langchain_core.messages import BaseMessage
import os
import logging
from typing import Annotated, Sequence
from langgraph.graph import add_messages
from typing import Literal
# Setup logging
logging.basicConfig(level=logging.DEBUG)

class AgentState(TypedDict):
    messages: Annotated[Sequence[BaseMessage], add_messages] # Use dict if not using BaseMessage instances
    repo_path: Optional[str]
    combined_file_path: Optional[str]
    aws_identity: Optional[str]
    
class GraphConfig(TypedDict):
    model: Literal['openai', 'anthropic']
# Step 2: Define Functions
def initialize_repo_and_aws(state: AgentState) -> None:
    """
    Sequentially clone the repository and configure AWS.
    """
    print(f"Initializing repo and AWS with state: {state}")
    try:
        logging.debug("Cloning repository...")
        repo_path = clone_repository()
        state["repo_path"] = repo_path
        logging.debug(f"Repository cloned at: {repo_path}")

        logging.debug("Configuring AWS...")
        aws_config = configure_aws_from_env()
        state["aws_identity"] = aws_config["identity"]
        logging.debug(f"AWS Identity: {aws_config['identity']}")

        new_messages = state["messages"] + [{"content": "Initialized repository and configured AWS."}]
        state["messages"] = new_messages

    except Exception as e:
        logging.error(f"Error in initialize_repo_and_aws: {e}")


def combine_files(state: AgentState) -> None:
    """
    Combine all files from the cloned repository into a single text file.
    """
    try:
        logging.debug("Combining repository files...")
        repo_path = state["repo_path"]
        combined_file_dir = os.path.join(repo_path, "../rag/knowledge_base/combined_files")
        create_combined_txt(repo_path, combined_file_dir)
        logging.debug(f"Combined files created at: {combined_file_dir}")

        combined_file_path = os.path.join(combined_file_dir, "repository_combined.txt")
        state["combined_file_path"] = combined_file_path
        logging.debug(f"Combined file path: {combined_file_path}")

        new_messages = state["messages"] + [{"content": "Files combined into a single text file."}]

    except Exception as e:
        logging.error(f"Error in combine_files: {e}")


def process_combined_file(state: AgentState) -> None:
    """
    Process the combined file to perform additional actions in the workflow.
    """
    try:
        combined_file_path = state["combined_file_path"]
        logging.debug(f"Processing combined file at {combined_file_path}...")

        new_messages = state["messages"] + [{"content": f"Processed combined file: {combined_file_path}"}]
        
    except Exception as e:
        logging.error(f"Error in process_combined_file: {e}")


# Step 3: Define LangGraph Workflow
def create_workflow_agent():
    """
    Create a LangGraph workflow agent for initializing and managing the IaC process.
    """
    # Initialize the state graph
    workflow = StateGraph(AgentState, config_schema=GraphConfig)

    # Add nodes for each step
    workflow.add_node("initialize_repo_and_aws", initialize_repo_and_aws)
    workflow.add_node("combine_files", combine_files)
    workflow.add_node("process_combined_file", process_combined_file)

    # Set entry point
    workflow.set_entry_point("initialize_repo_and_aws")

    # Add edges
    workflow.add_edge("initialize_repo_and_aws", "combine_files")
    workflow.add_edge("combine_files", "process_combined_file")
    workflow.add_edge("process_combined_file", END)

    # Compile the graph
    compiled_workflow = workflow.compile()

    return compiled_workflow

# Example Usage
if __name__ == "__main__":
    # Create the workflow agent
    app = create_workflow_agent()

    # Define the initial state
    initial_state = AgentState({
        "messages": ["You are a helpful assistant."],
        "repo_path": "placeholder",
        "combined_file_path": "",
        "aws_identity": ""
    })

    # Invoke the workflow
    inputs = {"state": initial_state}
    results = app.invoke(inputs)
    print(results)
