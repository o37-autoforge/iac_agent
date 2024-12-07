from typing import TypedDict, List, Union
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
from utils.state_utils import append_state_update

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
    files_to_edit: list[str]
    implementation_plan: str
    user_questions: list[dict]
    memory: dict

def manage_memory(existing: dict, updates: Union[dict, str]) -> dict:
    """Memory management reducer function"""
    if isinstance(updates, str) and updates == "CLEAR":
        return {}
    elif isinstance(updates, dict):
        return {**existing, **updates}
    return existing

class PlanningState(TypedDict):
    messages: Annotated[list, add_messages]
    memory: Annotated[dict, manage_memory]

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
    append_state_update(state["repo_path"], "planning", "Getting user query")
    user_query = input("\nWhat changes would you like to make to the infrastructure? ")
    state["query"] = user_query
    state["messages"].append({"role": "user", "content": user_query})
    return state

def ask_user_questions(state: AgentState) -> AgentState:
    """Generate and ask clarifying questions to the user"""
    append_state_update(state["repo_path"], "planning", "Generating clarifying questions")
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
    append_state_update(state["repo_path"], "planning", "Identifying files to modify")
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

    IMPORTANT: Return ONLY the file paths, one per line. Do not include any markdown formatting, descriptions, or explanations.
    Only include files that actually exist in the file tree.
    Example output format:
    main.tf
    variables.tf
    outputs.tf
    """

    class FilesToEditSchema(BaseModel):
        files: List[str] = Field(description="List of file paths relative to the repository root that need to be edited")

    model_with_structure = llm.with_structured_output(FilesToEditSchema)
    result = model_with_structure.invoke(prompt)
    
    # Filter out any files that don't exist in the file tree
    existing_files = []
    file_tree_lines = state["file_tree"].split('\n')
    file_tree_files = [line.strip() for line in file_tree_lines if not line.strip().endswith('/')]
    
    for file in result.files:
        file_path = file.strip()
        if any(line.strip().endswith(file_path) for line in file_tree_files):
            existing_files.append(file_path)
    
    state["files_to_edit"] = existing_files
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
    append_state_update(state["repo_path"], "planning", "Creating implementation plan")
    prompt = f"""As an Infrastructure as Code expert, create a detailed implementation plan.

    User's Query: {state["query"]}

    User's Clarifications:
    {json.dumps(state["user_questions"], indent=2)}

    Files to Edit:
    {json.dumps(state["files_to_edit"], indent=2)}

    Create a detailed implementation plan that focuses ONLY on the actual code changes needed.
    Do NOT include validation steps, testing steps, or application instructions.
    
    The plan should ONLY include:
    1. The exact code changes needed for each file
    2. Any new files that need to be created
    3. The specific configurations and values to use
    
    Format the plan to be clear and direct, focusing only on what code needs to be written or modified.
    Do not include any instructions about terraform commands, validation, or deployment.
    """

    model_with_structure = llm.with_structured_output(ImplementationPlanSchema)
    plan = model_with_structure.invoke(prompt)
    
    # Get planning file path from state or environment
    planning_file_path = state.get('planning_file_path')
    if not planning_file_path:
        planning_file_path = os.getenv('PLANNING_FILE_PATH')
    if not planning_file_path:
        planning_file_path = os.path.join(state["repo_path"], "planning", "implementation_plan.txt")
    
    # Ensure planning directory exists
    os.makedirs(os.path.dirname(planning_file_path), exist_ok=True)
    
    # Save plan to file
    with open(planning_file_path, "w") as f:
        f.write(plan.plan)
    
    print(f"\nImplementation plan has been written to: {planning_file_path}")
    print("You can now review and modify the plan if needed.")
    
    while True:
        user_input = input("\nHave you finished reviewing/modifying the plan? (y/n): ").lower()
        if user_input == 'y':
            # Read potentially modified plan
            with open(planning_file_path, "r") as f:
                modified_plan = f.read()
            state["implementation_plan"] = modified_plan
            state["planning_file_path"] = planning_file_path  # Store the path in state
            break
        elif user_input == 'n':
            print("\nTake your time to review the plan. Press 'y' when ready.")
        else:
            print("Please enter 'y' or 'n'")
    
    return state

def create_planning_agent():
    """Create the planning workflow"""
    workflow = StateGraph(AgentState)

    # Add only necessary nodes
    workflow.add_node("get_query", get_user_query)
    workflow.add_node("ask_questions", ask_user_questions)
    workflow.add_node("identify_files", identify_files_to_edit)
    workflow.add_node("create_plan", create_implementation_plan)

    # Set entry point
    workflow.set_entry_point("get_query")

    # Add edges
    workflow.add_edge("get_query", "ask_questions")
    workflow.add_edge("ask_questions", "identify_files")
    workflow.add_edge("identify_files", "create_plan")
    workflow.add_edge("create_plan", END)

    return workflow.compile()

def start_planning(
    repo_path: str,
    codebase_overview: str,
    file_tree: str,
    file_descriptions: dict,
    subprocess_handler=None,
    forge_interface=None,
    planning_file_path=None
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
        "files_to_edit": [],
        "implementation_plan": "",
        "user_questions": [],
        "memory": {},
        "planning_file_path": planning_file_path
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