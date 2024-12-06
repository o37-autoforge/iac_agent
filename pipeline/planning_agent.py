from typing import TypedDict, List
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field
import json
import os
from pathlib import Path
from utils import subprocess_handler, forge_interface
from dotenv import load_dotenv
import logging
from typing import Annotated
from langgraph.graph import StateGraph, END
from langgraph.graph import add_messages

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

# Load environment variables
load_dotenv()

# Initialize LLM
llm = ChatOpenAI(model="gpt-4o", temperature=0)

class AgentState(TypedDict):
    messages: Annotated[list, add_messages]
    query: str
    repo_path: str
    file_descriptions: dict
    file_tree: str
    codebase_overview: str
    edit_decision: str
    files_to_edit: list[str]
    implementation_plan: str
    aws_info_needed: str
    aws_info_query: str
    aws_info_retrieved: str
    codebase_info_needed: str
    codebase_info_query: str
    codebase_info_retrieved: str
    user_questions: list[dict]

class UserQuestion(BaseModel):
    question: str = Field(..., description="The question to ask the user.")
    context: str = Field(..., description="Context to help the user understand the question.")
    default: str = Field(..., description="A reasonable default answer.")

class GeneratedQuestionsSchema(BaseModel):
    questions: List[UserQuestion]

class UserResponse(BaseModel):
    question: UserQuestion
    response: str = Field(..., description="The user's response to the question.")

class EditDecisionSchema(BaseModel):
    decision: str = Field(description="A 'yes' or 'no' answer to whether the codebase needs editing to execute the query.")

class AWSInfoNeededSchema(BaseModel):
    needed: str = Field(description="A 'yes' or 'no' answer to whether AWS information is needed.")
    query: str = Field(description="The query to get the needed AWS information if needed.", default="")

class CodebaseInfoNeededSchema(BaseModel):
    needed: str = Field(description="A 'yes' or 'no' answer to whether additional codebase information is needed.")
    query: str = Field(description="The query to get the needed codebase information if needed.", default="")

class ImplementationPlanSchema(BaseModel):
    plan: str = Field(description="A detailed, step-by-step plan to implement the user's query.")

def get_user_query(state: AgentState) -> AgentState:
    """Get the user's query about what changes they want to make"""
    user_query = input("\nWhat changes would you like to make to the infrastructure? ")
    state["query"] = user_query
    state["messages"].append({"role": "user", "content": user_query})
    return state

def ask_user_questions(state: AgentState) -> AgentState:
    """Generate and ask clarifying questions to the user"""
    prompt = f"""
    You are an expert in Infrastructure as Code (IaC) acting as a copilot. Generate questions to clarify the user's request.

    User's Query: {state["query"]}

    Current Codebase Overview:
    {state["codebase_overview"]}

    Generate questions to clarify any ambiguities and gather necessary details. Focus on:
    1. Specific requirements and constraints
    2. Resource naming conventions
    3. Configuration preferences
    4. Dependencies and integrations
    5. Security and compliance requirements

    Return a JSON object with questions following this format:
    {{
        "questions": [
            {{
                "question": "The specific question",
                "context": "Why this question matters",
                "default": "A sensible default answer"
            }}
        ]
    }}
    """
    
    model_with_structure = llm.with_structured_output(GeneratedQuestionsSchema)
    questions = model_with_structure.invoke(prompt)

    responses = []
    print("\nI need some clarification to better understand your requirements:")

    for i, question in enumerate(questions.questions, 1):
        print(f"\nQuestion {i}: {question.question}")
        print(f"Context: {question.context}")
        print(f"Default: {question.default}")

        while True:
            response = input("\nYour answer (press Enter for default): ").strip()
            if not response:
                response = question.default
                print(f"Using default: {response}")

            confirm = input("Confirm this answer? (y/n): ").lower()
            if confirm == 'y':
                break
            print("Let's try again...")

        responses.append({
            "question": question.question,
            "response": response
        })

    state["user_questions"] = responses
    return state

def determine_edit_needs(state: AgentState) -> AgentState:
    """Determine if we need to edit the codebase"""
    prompt = f"""
    You are an expert in Infrastructure as Code (IaC).

    User's Query: {state["query"]}

    User's Clarifications:
    {json.dumps(state["user_questions"], indent=2)}

    Codebase Overview:
    {state["codebase_overview"]}

    Based on the user's request and the current codebase, determine if we need to make edits to the code.
    Output 'yes' if any changes are needed, 'no' if the request can be handled without code changes.
    """

    model_with_structure = llm.with_structured_output(EditDecisionSchema)
    decision = model_with_structure.invoke(prompt)
    state["edit_decision"] = decision.decision
    return state

def identify_files_to_edit(state: AgentState) -> AgentState:
    """Identify which files need to be edited"""
    prompt = f"""
    You are an expert in Infrastructure as Code (IaC).

    User's Query: {state["query"]}

    User's Clarifications:
    {json.dumps(state["user_questions"], indent=2)}

    File Tree:
    {state["file_tree"]}

    File Descriptions:
    {json.dumps(state["file_descriptions"], indent=2)}

    Based on the user's request and the codebase structure, identify which files need to be edited.
    Consider:
    1. Files that directly implement the requested changes
    2. Files that might be affected by the changes
    3. Configuration files that need updating
    4. Files containing related resources or dependencies

    Return a list of file paths relative to the repository root.
    """

    files = llm.invoke(prompt).content.strip().split('\n')
    state["files_to_edit"] = [f.strip() for f in files if f.strip()]
    return state

def check_aws_info_needed(state: AgentState) -> AgentState:
    """Determine if we need to fetch information from AWS"""
    prompt = f"""
    You are an expert in Infrastructure as Code (IaC).

    User's Query: {state["query"]}

    User's Clarifications:
    {json.dumps(state["user_questions"], indent=2)}

    Files to Edit:
    {json.dumps(state["files_to_edit"], indent=2)}

    Determine if we need to fetch any information from AWS to implement these changes.
    If yes, also specify what information we need to query.

    Consider:
    1. Current resource states
    2. Dependencies
    3. Available resources
    4. Configuration values
    5. Network settings
    """

    model_with_structure = llm.with_structured_output(AWSInfoNeededSchema)
    result = model_with_structure.invoke(prompt)
    state["aws_info_needed"] = result.needed
    state["aws_info_query"] = result.query
    return state

def check_codebase_info_needed(state: AgentState) -> AgentState:
    """Determine if we need additional information from the codebase"""
    prompt = f"""
    You are an expert in Infrastructure as Code (IaC).

    User's Query: {state["query"]}

    User's Clarifications:
    {json.dumps(state["user_questions"], indent=2)}

    Files to Edit:
    {json.dumps(state["files_to_edit"], indent=2)}

    Determine if we need to fetch any additional information from the codebase.
    If yes, also specify what information we need to query.

    Consider:
    1. Resource configurations
    2. Variable definitions
    3. Module usage
    4. Dependencies
    5. Configuration patterns
    """

    model_with_structure = llm.with_structured_output(CodebaseInfoNeededSchema)
    result = model_with_structure.invoke(prompt)
    state["codebase_info_needed"] = result.needed
    state["codebase_info_query"] = result.query
    return state

def create_implementation_plan(state: AgentState) -> AgentState:
    """Create a detailed implementation plan"""
    prompt = f"""
    You are an expert in Infrastructure as Code (IaC).

    User's Query: {state["query"]}

    User's Clarifications:
    {json.dumps(state["user_questions"], indent=2)}

    Files to Edit:
    {json.dumps(state["files_to_edit"], indent=2)}

    Create a detailed, step-by-step implementation plan. 
    
    IMPORTANT: As Step 0, list any specific information that will be needed from either:
    a) The AWS infrastructure (current state, configurations, resources, etc.)
    b) The codebase (variable definitions, module configurations, etc.)
    
    Format Step 0 as:
    Step 0: Information Requirements
    AWS Information Needed:
    - [List specific AWS information needed, or "None" if nothing is needed]
    
    Codebase Information Needed:
    - [List specific codebase information needed, or "None" if nothing is needed]

    Then continue with the implementation steps:
    1. Specific files to modify
    2. Changes to make in each file
    3. Order of operations
    4. Required validations
    5. Testing steps
    6. Rollback considerations
    """

    model_with_structure = llm.with_structured_output(ImplementationPlanSchema)
    plan = model_with_structure.invoke(prompt)
    state["implementation_plan"] = plan.plan

    # Now determine if we need AWS information
    aws_info_prompt = f"""
    Based on the implementation plan's Step 0, determine if AWS information is needed.
    If yes, create a specific query to retrieve that information.

    Implementation Plan:
    {plan.plan}

    Output 'yes' if AWS information is needed and provide the specific query.
    Output 'no' if no AWS information is needed.
    """
    model_with_structure = llm.with_structured_output(AWSInfoNeededSchema)
    aws_result = model_with_structure.invoke(aws_info_prompt)
    state["aws_info_needed"] = aws_result.needed
    state["aws_info_query"] = aws_result.query if aws_result.needed == "yes" else ""
    state["aws_info_retrieved"] = ""  # Placeholder for now

    # Determine if we need codebase information
    codebase_info_prompt = f"""
    Based on the implementation plan's Step 0, determine if additional codebase information is needed.
    If yes, create a specific query to retrieve that information.

    Implementation Plan:
    {plan.plan}

    Output 'yes' if codebase information is needed and provide the specific query.
    Output 'no' if no codebase information is needed.
    """
    model_with_structure = llm.with_structured_output(CodebaseInfoNeededSchema)
    codebase_result = model_with_structure.invoke(codebase_info_prompt)
    state["codebase_info_needed"] = codebase_result.needed
    state["codebase_info_query"] = codebase_result.query if codebase_result.needed == "yes" else ""
    state["codebase_info_retrieved"] = ""  # Placeholder for now

    return state

def create_planning_agent():
    """Create the planning workflow"""
    workflow = StateGraph(AgentState)

    # Add nodes
    workflow.add_node("get_query", get_user_query)
    workflow.add_node("ask_questions", ask_user_questions)
    workflow.add_node("determine_edits", determine_edit_needs)
    workflow.add_node("identify_files", identify_files_to_edit)
    workflow.add_node("check_aws", check_aws_info_needed)
    workflow.add_node("check_codebase", check_codebase_info_needed)
    workflow.add_node("create_plan", create_implementation_plan)

    # Set entry point
    workflow.set_entry_point("get_query")

    # Add edges
    workflow.add_edge("get_query", "ask_questions")
    workflow.add_edge("ask_questions", "determine_edits")

    # Conditional edges for edit decision
    workflow.add_conditional_edges(
        "determine_edits",
        lambda x: x["edit_decision"],
        {
            "yes": "identify_files",
            "no": END
        }
    )

    workflow.add_edge("identify_files", "check_aws")
    workflow.add_edge("check_aws", "check_codebase")
    workflow.add_edge("check_codebase", "create_plan")
    workflow.add_edge("create_plan", END)

    return workflow.compile()

def start_planning(
    repo_path: str,
    codebase_overview: str,
    file_tree: str,
    file_descriptions: dict,
    subprocess_handler=None,
    forge_interface=None
) -> dict:
    """Start the planning process"""
    workflow = create_planning_agent()
    
    initial_state = AgentState({
        "messages": [],
        "query": "",
        "repo_path": repo_path,
        "file_descriptions": file_descriptions,
        "file_tree": file_tree,
        "codebase_overview": codebase_overview,
        "edit_decision": "",
        "files_to_edit": [],
        "implementation_plan": "",
        "aws_info_needed": "",
        "aws_info_query": "",
        "aws_info_retrieved": "",
        "codebase_info_needed": "",
        "codebase_info_query": "",
        "codebase_info_retrieved": "",
        "user_questions": []
    })

    final_state = workflow.invoke(initial_state)
    return final_state

if __name__ == "__main__":
    # This is only for standalone testing
    repo_path = os.path.join(os.getcwd(), 'repo')
    
    # Read analysis files
    analysis_dir = os.path.join(repo_path, 'analysis')
    
    with open(os.path.join(analysis_dir, 'codebase_overview.txt')) as f:
        codebase_overview = f.read()
    
    with open(os.path.join(analysis_dir, 'file_tree.txt')) as f:
        file_tree = f.read()
    
    # Build file descriptions dictionary
    file_descriptions = {}
    for file in os.listdir(analysis_dir):
        if file.endswith('_analysis.txt'):
            original_file = file.replace('_analysis.txt', '')
            with open(os.path.join(analysis_dir, file)) as f:
                file_descriptions[original_file] = f.read()
    
    # Start the planning process
    final_state = start_planning(
        repo_path,
        codebase_overview,
        file_tree,
        file_descriptions
    )
    
    # Print the implementation plan
    print("\nImplementation Plan:")
    print("=" * 80)
    print(final_state["implementation_plan"])
    print("=" * 80) 