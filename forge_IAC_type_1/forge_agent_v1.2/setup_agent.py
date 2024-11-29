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
import asyncio
from pathlib import Path
import json

# Setup logging
# logging.basicConfig(level=logging.CRITICAL)
llm = ChatOpenAI(model="gpt-4o", temperature=0)
from utils.subprocess_handler import SubprocessHandler
from utils.forge_interface import ForgeInterface
subprocess_handler = SubprocessHandler(os.getenv("REPO_PATH"))
forge_interface = ForgeInterface(subprocess_handler)

class AgentState(TypedDict):
    messages: Annotated[list, add_messages] # Use dict if not using BaseMessage instances
    query: str
    repo_path: str
    combined_file_path: str 
    aws_identity: str
    file_descriptions: dict
    file_tree: str
    codebase_overview: str
    edit_code_decision: str
    files_to_edit: list[str]
    implementation_plan: str
    execute_commands: str
    aws_info_query: str
    info_retrieval_query: str
    retrieve_info: str
    memory: dict

class UserQuestion(BaseModel):
    question: str = Field(..., description="The question to ask the user.")
    context: str = Field(..., description="Context to help the user understand the question.")
    default: str = Field(..., description="A reasonable default answer.")

class GeneratedQuestionsSchema(BaseModel):
    questions: List[UserQuestion]

class UserResponse(BaseModel):
    question: UserQuestion
    response: str = Field(..., description="The user's response to the question.")

class EditCodeDecisionSchema(BaseModel):
    decision: str = Field(description="A 'yes' or 'no' answer to whether the codebase needs editing to execute the query.")

class ImplementationPlanSchema(BaseModel):
    plan: str = Field(description="A detailed, step-by-step plan to implement the user's query.")

class CommandExecutionSchema(BaseModel):
    execute_commands: str = Field(description="A 'yes' or 'no' answer to whether commands need to be executed.")

def generate_user_questions(state: AgentState) -> dict:
    prompt = f"""
        You are an expert in Infrastructure as Code (IaC) acting as a copilot. Your job is to generate specific questions that need to be answered by the user before implementing their request.

        User's Query: {state["query"]}

        Based on the specific query, generate questions that will help clarify any ambiguities or gather necessary details to proceed.

        **Output Format:**

        Return a JSON object with a single key `"questions"`, which is a list of question objects. Each question object should have the following keys:
        - `question`: The specific question to ask.
        - `context`: Important context that helps the user understand why this question matters.
        - `default`: A reasonable default answer as a string (use JSON-formatted strings for complex values).

        Generate as many questions as needed, but focus on the most critical information needed. Do not include questions that are not related to the query. Do not over do it. 

        **Example Output:**

        ```json
        {{
            "questions": [
                {{
                    "question": "Which cloud provider should the infrastructure be deployed to?",
                    "context": "Different cloud providers have specific configurations and services.",
                    "default": "AWS"
                }},
                {{
                    "question": "Do you have any naming conventions for resources?",
                    "context": "Consistent naming helps in resource management and identification.",
                    "default": "project-name-resource-type"
                }}
            ]
        }} """
    
    model_with_structure = llm.with_structured_output(GeneratedQuestionsSchema) 
    structured_output = model_with_structure.invoke(prompt) 

    responses = []
    print("\nPlease answer the following questions to help me understand your requirements:")

    for i, question in enumerate(structured_output.questions, 1):
        print(f"\nQuestion {i}: {question.question}")
        print(f"Context: {question.context}")
        print(f"Default answer: {question.default}")

        while True:
            response = input("\nYour answer (press Enter to use default): ").strip()
            if not response:
                response = question.default
                print(f"Using default answer: {response}")

            confirm = input("Confirm this answer? (y/n): ").lower()
            if confirm == 'y':
                break
            print("Let's try again...")

        responses.append(UserResponse(question=question, response=response))

    responses = [resp.model_dump() for resp in responses]

    responses = [
            {
                "question": resp['question']['question'],
                "response": resp['response']
            } for resp in responses
        ]
    
    responses = json.dumps(responses, indent=2)


    state["user_questions"] = responses
    return state

def determine_command_execution(state: AgentState) -> dict:
    """
    Determine if we nede to retrieve information from AWS.

    """
    relevant_file_contents = "\n".join([open(state["repo_path"] + "/" + f, "r").read() for f in state["files_to_edit"]])

    prompt = f"""
    You are an expert in Infrastructure as Code (IaC). 
    
    Given the query and relevant and the current codebase contents, determine if we need to retrieve any information from AWS. Output either 'yes' or 'no'.

    Query: {state["query"]}

    Codebase Contents:
    {relevant_file_contents}

    """
    model_with_structure = llm.with_structured_output(CommandExecutionSchema)
    structured_output = model_with_structure.invoke(prompt)
    state["execute_commands"] = structured_output.execute_commands
    state["messages"].append({"role": "system", "content": f"Command execution decision: {state['execute_commands']}."})
    
    return state

class AWSInfoQuerySchema(BaseModel):
    aws_info_query: str = Field(description="Query to retrieve information from AWS if needed.")

def retrieve_aws_info(state: AgentState) -> dict:
    """
    Create a query to retrieve information from AWS if needed.
    """
    relevant_file_contents = "\n".join([open(state["repo_path"] + "/" + f, "r").read() for f in state["files_to_edit"]])

    prompt = f"""
    You are an expert in Infrastructure as Code (IaC) and are trying to implement the following query: {state["query"]}. You have the
    ability to retrieve information from AWS. Determine the query needed to retrieve the necessary information from AWS, and output it.

    Query: {state["query"]}

    Codebase Contents:
    {relevant_file_contents}

    """
    model_with_structure = llm.with_structured_output(AWSInfoQuerySchema)
    structured_output = model_with_structure.invoke(prompt)
    state["aws_info_query"] = structured_output.aws_info_query
    state["messages"].append({"role": "system", "content": f"AWS info query: {state['aws_info_query']}."})

    return state

def initialize_repo_and_aws(state: AgentState) -> dict:
    """
    Sequentially clone the repository and configure AWS.
    """
    clone_repository(state)
    configure_aws_from_env(state)
    state["messages"].append({"role": "system", "content": "Initialized repository and configured AWS."})

    return state 

class InfoRetrievalQuerySchema(BaseModel):
    info_retrieval_query: str = Field(description="Query to retrieve specific information from the codebase if needed.")

def retrieve_codebase_info(state: AgentState) -> None:
    """
    Create a query to retrieve specific information from the codebase if needed.
    """
    relevant_file_contents = "\n".join([open(state["repo_path"] + "/" + f, "r").read() for f in state["files_to_edit"]])

    prompt = f"""
    You are an expert in Infrastructure as Code (IaC). You have the ability to retrieve information from the codebase in natural language. 
     
    Given the query, create and output another query to retrieve the necessary information from the codebase. 

    Query: {state["query"]}

    Codebase Contents:
    {relevant_file_contents}

    """
    model_with_structure = llm.with_structured_output(InfoRetrievalQuerySchema)
    structured_output = model_with_structure.invoke(prompt)
    state["info_retrieval_query"] = structured_output.info_retrieval_query
    state["messages"].append({"role": "system", "content": f"Info retrieval query: {state['info_retrieval_query']}."})
    return state

class InfoRetrievalSchema(BaseModel):
    retrieve_info: str = Field(description="A 'yes' or 'no' answer to whether information needs to be retrieved from the codebase.")

def determine_info_retrieval(state: AgentState) -> dict:
    """
    Determine if specific information needs to be retrieved from the codebase.
    """
    relevant_file_contents = "\n".join([open(state["repo_path"] + "/" + f, "r").read() for f in state["files_to_edit"]])

    prompt = f"""
    You are an expert in Infrastructure as Code (IaC).
     
    Given the query, and the current codebase contents, determine if we need to retrieve any specific information from the codebase. Output either 'yes' or 'no'.

    Query: {state["query"]}

    Codebase Contents:
    {relevant_file_contents}


    """
    model_with_structure = llm.with_structured_output(InfoRetrievalSchema)
    structured_output = model_with_structure.invoke(prompt)
    state["retrieve_info"] = structured_output.retrieve_info
    state["messages"].append({"role": "system", "content": f"Information retrieval decision: {state['retrieve_info']}."})
    
    return state

def combine_files(state: AgentState) -> dict:
    """
    Combine all files from the cloned repository into a single text file.
    """

    create_combined_txt(state)
    state["messages"].append({"role": "system", "content": "Files combined into a single text file."})

    return state 

def create_aws_data_tree(state: AgentState) -> dict:


    """
    Create a data tree for AWS data.
    """

    setup_AWS_state(state)
    state["messages"].append({"role": "system", "content": "AWS Raggable data tree created."})

    return state 

def describe_files(state: AgentState) -> dict:
    """
    Use an LLM to generate natural language descriptions for code files.
    """
    file_descriptions = generate_file_descriptions(state["repo_path"])
    state["file_descriptions"] = file_descriptions
    state["messages"].append({"role": "system", "content": "Generated natural language descriptions for each code file."})
    return state

def create_codebase_overview(state: AgentState) -> dict:


    """
    Use Gemini to generate a natural language overview of the codebase.
    """   
    generate_file_tree(state)
    codebase_overview = generate_codebase_overview(state["combined_file_path"])
    state["codebase_overview"] = codebase_overview
    state["messages"].append({"role": "system", "content": "Generated a natural language overview of the codebase."})
    return state

def make_edit_decision(state: AgentState) -> dict:
    # """
    # Decide whether the codebase needs editing to execute the query.
    # """
    # file_descriptions = state["file_descriptions"]
    # prompt = f"""
    # You are an expert in Infrastructure as Code (IaC).

    # We are trying to implement the following query: {state["query"]}


    # File Descriptions:
    # {file_descriptions}

    # """
    # model_with_structure = llm.with_structured_output(EditCodeDecisionSchema)
    # structured_output = model_with_structure.invoke(prompt)
    state["edit_code_decision"] = "yes"
    state["messages"].append({"role": "system", "content": f"Edit decision: {state['edit_code_decision']}."})
    
    # Return the updated state
    return state

def identify_files_to_edit(state: AgentState) -> dict:
    """
    Identify the files that need editing to implement the query.
    """
    file_descriptions = state["file_descriptions"]
    files_to_edit = choose_relevant_IaC_files(file_descriptions, state["query"], state["file_tree"])
    state["files_to_edit"] = files_to_edit
    state["messages"].append({"role": "system", "content": f"Files to edit: {state['files_to_edit']}."})
    return state

def generate_file_tree(state: AgentState) -> dict:
    """
    Generate a file tree of the repository and save it to the state.
    """
    repo_path = state["repo_path"]
    file_tree = []

    for root, dirs, files in os.walk(repo_path):
        level = root.replace(repo_path, '').count(os.sep)
        indent = ' ' * 4 * level
        file_tree.append(f"{indent}{os.path.basename(root)}/")
        subindent = ' ' * 4 * (level + 1)
        for f in files:
            file_tree.append(f"{subindent}{f}")

    state["file_tree"] = "\n".join(file_tree)
    state["messages"].append({"role": "system", "content": "Generated file tree of the repository."})

    return state

def plan_implementation(state: AgentState) -> dict:
    """
    Create a step-by-step implementation plan for the query.
    """
    relevant_file_contents = "\n".join([open(state["repo_path"] + "/" + f, "r").read() for f in state["files_to_edit"]])
   
    prompt = f"""
    You are an expert in Infrastructure as Code (IaC). Given the following file contents and the query, 
    create an ultra-specific, step-by-step plan to implement the query. Make sure you reference the file tree, 
    and the file names to create the plan. Furthemrore, ensure that your implementation plan is specific to the codebase, and keeps the 
    overall codebase structure in mind. Fiinally, also take into account the user's responses to the questions that were created earlier. 

    Query: {state["query"]}

    File Contents:
    {relevant_file_contents}

    File Tree:
    {state["file_tree"]}

    User Questions:
    {state["user_questions"]}
    """
    model_with_structure = llm.with_structured_output(ImplementationPlanSchema)
    structured_output = model_with_structure.invoke(prompt)
    state["implementation_plan"] = structured_output.plan
    state["messages"].append({"role": "system", "content": "Implementation plan created."})
    return state


def start_forge_process(state: AgentState):
    """
    Start the forge process with relevant files.
    """
    if state["files_to_edit"]:
        relevant_files = [Path(state["repo_path"]) / f for f in state["files_to_edit"]]
        subprocess_handler.start_forge(os.getenv("OPENAI_API_KEY"), relevant_files)

def get_user_query(state: AgentState):
    """
    Capture the user's query via CLI.
    """
    user_query = input("Please enter your query: ")
    state["query"] = user_query
    state["messages"].append({"role": "user", "content": user_query})

def create_workflow_agent():
    workflow = StateGraph(AgentState)

    # Add nodes for each step
    workflow.add_node("initialize_repo_and_aws", initialize_repo_and_aws)
    workflow.add_node("combine_files", combine_files)
    workflow.add_node("generate_file_tree", generate_file_tree)
    workflow.add_node("create_aws_data_tree", create_aws_data_tree)
    workflow.add_node("create_codebase_overview", create_codebase_overview)
    workflow.add_node("describe_files", describe_files)
    workflow.add_node("get_user_query", get_user_query)  # Human-in-the-loop node
    workflow.add_node("make_edit_decision", make_edit_decision)
    workflow.add_node("identify_files_to_edit", identify_files_to_edit)
    workflow.add_node("determine_command_execution", determine_command_execution)
    workflow.add_node("retrieve_aws_info", retrieve_aws_info)
    workflow.add_node("determine_info_retrieval", determine_info_retrieval)
    workflow.add_node("retrieve_codebase_info", retrieve_codebase_info)
    workflow.add_node("plan_implementation", plan_implementation)

    # Set entry point
    workflow.set_entry_point("initialize_repo_and_aws")

    # Add edges
    workflow.add_edge("initialize_repo_and_aws", "combine_files")
    workflow.add_edge("combine_files", "generate_file_tree")
    workflow.add_edge("generate_file_tree", "create_aws_data_tree")
    workflow.add_edge("create_aws_data_tree", "create_codebase_overview")
    workflow.add_edge("create_codebase_overview", "describe_files")
    workflow.add_edge("describe_files", "get_user_query")  # Transition to user query

    # Ensure make_edit_decision is reachable
    workflow.add_edge("get_user_query", "make_edit_decision")

    # Conditional edge for edit decision
    workflow.add_conditional_edges("make_edit_decision", lambda state: state["edit_code_decision"], {
        "yes": "identify_files_to_edit",
        "no": END
    })

    workflow.add_edge("identify_files_to_edit", "determine_command_execution")

    # Conditional edges for command execution
    workflow.add_conditional_edges("determine_command_execution", lambda state: state["execute_commands"], {
        "yes": "retrieve_aws_info",
        "no": "determine_info_retrieval"
    })

    # Conditional edges for information retrieval
    workflow.add_conditional_edges("determine_info_retrieval", lambda state: state["retrieve_info"], {
        "yes": "retrieve_codebase_info",
        "no": "plan_implementation"
    })

    workflow.add_edge("retrieve_aws_info", "determine_info_retrieval")
    workflow.add_edge("retrieve_codebase_info", "plan_implementation")
    workflow.add_edge("plan_implementation", END)

    return workflow.compile()

def start_setup():
    app = create_workflow_agent()
    initial_state = AgentState({
        "messages": [],
        "query": "",
        "repo_path": "",
        "combined_file_path": "",
        "aws_identity": "",
        "file_descriptions": dict,
        "codebase_overview": "",
        "edit_code_decision": "",
        "files_to_edit": [],
        "implementation_plan": "",
        "file_tree": str,
        "execute_commands": "",
        "aws_info_query": "",
        "info_retrieval_query": "",
        "retrieve_info": "",
        "memory": dict,
        "user_questions": []

    })
    for event in app.stream(initial_state):
        print(event)

    return initial_state, subprocess_handler, forge_interface

if __name__ == "__main__":
    start_setup()

    