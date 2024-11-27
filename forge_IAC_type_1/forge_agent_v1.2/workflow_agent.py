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
from utils.workflow_utils import setup_AWS_state, generate_file_descriptions, generate_codebase_overview
from rag_agent import choose_relevant_IaC_files, choose_relevant_aws_files, retrieve_information
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

# Setup logging
# logging.basicConfig(level=logging.CRITICAL)
llm = ChatOpenAI(model="gpt-4o", temperature=0)

class AgentState(TypedDict):
    messages: Annotated[list, add_messages] # Use dict if not using BaseMessage instances
    query: str
    repo_path: str
    combined_file_path: str
    aws_identity: str
    file_descriptions: dict
    codebase_overview: str
    edit_code_decision: str
    files_to_edit: list[str]
    implementation_plan: str

# Define structured output schemas
class EditCodeDecisionSchema(BaseModel):
    decision: str = Field(description="A 'yes' or 'no' answer to whether the codebase needs editing to execute the query.")

class ImplementationPlanSchema(BaseModel):
    plan: str = Field(description="A detailed, step-by-step plan to implement the user's query.")

# Step 2: Define Functions
def initialize_repo_and_aws(state: AgentState) -> AgentState:
    """
    Sequentially clone the repository and configure AWS.
    """
    clone_repository(state)
    configure_aws_from_env(state)
    state["messages"].append({"role": "system", "content": "Initialized repository and configured AWS."})

    return state 

def combine_files(state: AgentState) -> None:
    """
    Combine all files from the cloned repository into a single text file.
    """

    create_combined_txt(state)
    state["messages"].append({"role": "system", "content": "Files combined into a single text file."})

    return state 

def create_aws_data_tree(state: AgentState) -> None:


    """
    Create a data tree for AWS data.
    """

    setup_AWS_state(state)
    state["messages"].append({"role": "system", "content": "AWS Raggable data tree created."})

    return state 

# Step 3: Define LangGraph Workflow

def describe_files(state: AgentState) -> None:
    """
    Use an LLM to generate natural language descriptions for code files.
    """
    file_descriptions = generate_file_descriptions(state["repo_path"])
    state["file_descriptions"] = file_descriptions
    state["messages"].append({"role": "system", "content": "Generated natural language descriptions for each code file."})
    return state

def create_codebase_overview(state: AgentState) -> None:


    """
    Use Gemini to generate a natural language overview of the codebase.
    """
    codebase_overview = generate_codebase_overview(state["combined_file_path"])
    state["codebase_overview"] = codebase_overview
    state["messages"].append({"role": "system", "content": "Generated a natural language overview of the codebase."})
    return state

def make_edit_decision(state: AgentState) -> None:
    """
    Decide whether the codebase needs editing to execute the query.
    """
    file_descriptions = state["file_descriptions"]
    prompt = f"""
    You are an expert in Infrastructure as Code (IaC). Analyze the following file descriptions and the query 
    to determine if the codebase needs editing.

    Query: {query}

    File Descriptions:
    {file_descriptions}
    """
    model_with_structure = llm.with_structured_output(EditCodeDecisionSchema)
    structured_output = model_with_structure.invoke(prompt)
    state["edit_code_decision"] = structured_output.decision
    state["messages"].append({"role": "system", "content": f"Edit decision: {state['edit_code_decision']}."})
    return state

def identify_files_to_edit(state: AgentState) -> None:
    """
    Identify the files that need editing to implement the query.
    """
    file_descriptions = state["file_descriptions"]
    print(file_descriptions)
    files_to_edit = choose_relevant_IaC_files(file_descriptions, state["query"])
    state["files_to_edit"] = files_to_edit
    state["messages"].append({"role": "system", "content": f"Files to edit: {state['files_to_edit']}."})
    return state

def plan_implementation(state: AgentState) -> None:
    """
    Create a step-by-step implementation plan for the query.
    """
    relevant_file_contents = "\n".join([open(f, "r").read() for f in state["files_to_edit"]])
    prompt = f"""
    You are an expert in Infrastructure as Code (IaC). Given the following file contents and the query, 
    create an ultra-specific, step-by-step plan to implement the query.

    Query: {state["query"]}

    File Contents:
    {relevant_file_contents}
    """
    model_with_structure = llm.with_structured_output(ImplementationPlanSchema)
    structured_output = model_with_structure.invoke(prompt)
    state["implementation_plan"] = structured_output.plan
    state["messages"].append({"role": "system", "content": "Implementation plan created."})
    return state

def create_workflow_agent():
    """
    Create a LangGraph workflow agent for analyzing and modifying IaC codebases.
    """
    workflow = StateGraph(AgentState)

    # Add nodes for each step
    workflow.add_node("initialize_repo_and_aws", initialize_repo_and_aws)
    workflow.add_node("combine_files", combine_files)
    workflow.add_node("create_aws_data_tree", create_aws_data_tree)
    workflow.add_node("create_codebase_overview", create_codebase_overview)
    workflow.add_node("describe_files", describe_files)
    workflow.add_node("make_edit_decision", make_edit_decision)
    workflow.add_node("identify_files_to_edit", identify_files_to_edit)
    workflow.add_node("plan_implementation", plan_implementation)

    # Set entry point
    workflow.set_entry_point("initialize_repo_and_aws")

    # Add edges
    workflow.add_edge("initialize_repo_and_aws", "combine_files")
    workflow.add_edge("combine_files", "create_aws_data_tree")
    workflow.add_edge("create_aws_data_tree", "create_codebase_overview")
    workflow.add_edge("create_codebase_overview", "describe_files")
    workflow.add_edge("describe_files", "make_edit_decision")
    workflow.add_edge("make_edit_decision", "identify_files_to_edit")
    workflow.add_edge("identify_files_to_edit", "plan_implementation")
    workflow.add_edge("plan_implementation", END)

    return workflow.compile()


# Example Usage
if __name__ == "__main__":
    app = create_workflow_agent()

    initial_state = AgentState({
        "messages": [],
        "query": "Add a new EC2 instance configuration to the Terraform codebase.",
        "repo_path": "",
        "combined_file_path": "",
        "aws_identity": "",
        "file_descriptions": dict,
        "codebase_overview": "",
        "edit_code_decision": "",
        "files_to_edit": [],
        "implementation_plan": ""
    })

    query = "Add a new EC2 instance configuration to the Terraform codebase."
    initial_state["messages"].append({"role": "user", "content": query})

    for event in app.stream(initial_state):
        print(event)
