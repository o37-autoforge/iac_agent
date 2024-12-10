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
import re

# Configure logging
logging.basicConfig(
    level=logging.ERROR,
    format='%(asctime)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
# Disable all logging levels
logging.disable(logging.CRITICAL)
logger = logging.getLogger(__name__)


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

class NewFileSchema(BaseModel):
    files: List[dict] = Field(
        description="List of new files/folders that need to be created",
        default_factory=list
    )
    class Config:
        json_schema_extra = {
            "example": [
                {
                    "path": "modules/new_module/main.tf",
                    "is_directory": False,
                    "content": "# Terraform configuration..."
                }
            ]
        }

def get_user_query(state: AgentState) -> AgentState:
    """Get the user's query about what changes they want to make"""
    append_state_update(state["repo_path"], "planning", "get_user_query", "Getting user infrastructure request")
    user_query = input("\nWhat changes would you like to make to the infrastructure? ")
    state["query"] = user_query
    state["messages"].append({"role": "user", "content": user_query})
    return state

def ask_user_questions(state: AgentState) -> AgentState:
    """Generate and ask clarifying questions to the user"""
    append_state_update(state["repo_path"], "planning", "ask_user_questions", "Generating clarifying questions")
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
    append_state_update(state["repo_path"], "planning", "determine_edit_needs", "Analyzing required code changes")
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
    append_state_update(state["repo_path"], "planning", "identify_files", "Identifying affected files")
    logger.info("Starting file identification")
    
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
    2. Files that might be affected by the changes (dependencies)
    3. Configuration files that need updating
    4. Files containing related resources
    5. New files that need to be created

    Return a JSON object with:
    1. Existing files that need modification
    2. New files that need to be created
    3. Directories that need to be created
    """

    class FilesToEdit(BaseModel):
        existing_files: List[str] = Field(description="List of existing files that need to be modified")
        new_files: List[str] = Field(description="List of new files that need to be created")
        new_directories: List[str] = Field(description="List of new directories that need to be created")

    model_with_structure = llm.with_structured_output(FilesToEdit)
    result = model_with_structure.invoke(prompt)
    
    # Filter out any non-existent files from existing_files
    file_tree_lines = state["file_tree"].split('\n')
    file_tree_files = [line.strip() for line in file_tree_lines if not line.strip().endswith('/')]
    
    existing_files = []
    for file in result.existing_files:
        file_path = file.strip()
        # Remove any 'repo/' prefix if it exists
        if file_path.startswith('repo/'):
            file_path = file_path[5:]
            
        full_path = os.path.join(state["repo_path"], file_path)
        if os.path.exists(full_path) or any(line.strip().endswith(file_path) for line in file_tree_files):
            existing_files.append(file_path)
            logger.info(f"Identified existing file to edit: {file_path}")
        else:
            logger.warning(f"File not found: {full_path}")
    
    # Process new files and directories
    new_files = []
    for file in result.new_files:
        file_path = file.strip()
        if file_path.startswith('repo/'):
            file_path = file_path[5:]
        new_files.append(file_path)
    
    new_directories = []
    for directory in result.new_directories:
        dir_path = directory.strip()
        if dir_path.startswith('repo/'):
            dir_path = dir_path[5:]
        new_directories.append(dir_path)
    
    # Store both existing and new files in state
    state["files_to_edit"] = existing_files
    state["memory"]["new_files"] = [os.path.join(state["repo_path"], f) for f in new_files]
    state["memory"]["new_directories"] = [os.path.join(state["repo_path"], d) for d in new_directories]
    
    logger.info(f"Files to edit: {state['files_to_edit']}")
    logger.info(f"New files to create: {state['memory']['new_files']}")
    logger.info(f"New directories to create: {state['memory']['new_directories']}")
    
    return state

def check_aws_info_needed(state: AgentState) -> AgentState:
    """Determine if we need to fetch information from AWS"""
    append_state_update(state["repo_path"], "planning", "check_aws_info", "Checking AWS information requirements")
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
    append_state_update(state["repo_path"], "planning", "check_codebase_info", "Checking codebase information needs")
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
    append_state_update(state["repo_path"], "planning", "create_plan", "Creating detailed implementation plan")
    prompt = f"""As an Infrastructure as Code expert, create a detailed implementation plan.

    User's Query: {state["query"]}

    User's Clarifications:
    {json.dumps(state["user_questions"], indent=2)}

    Files to Edit:
    {json.dumps(state["files_to_edit"], indent=2)}

    Create a detailed implementation plan that focuses ONLY on the actual code changes needed.
    Do NOT include validation steps, testing steps, or application instructions. This implementation plan will be sent to a code execution AI agent to be executed. 
    
    The plan should ONLY include:

    1. The original User Query
    2. The exact code changes needed for each file
    3. Any new files that need to be created
    4. The specific configurations and values to use
    
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

def prepare_new_files(state: AgentState) -> AgentState:
    """Parse implementation plan and create any new files/folders needed"""
    append_state_update(state["repo_path"], "planning", "prepare_files", "Creating new files and directories")
    logger.info("Starting new file preparation")
    
    # Get forge instance from memory
    forge = state["memory"].get("forge")
    if not forge:
        logger.error("No forge instance found in memory")
        return state
    
    # Create new directories first
    new_directories = state["memory"].get("new_directories", [])
    for directory in new_directories:
        os.makedirs(directory, exist_ok=True)
        logger.info(f"Created directory: {directory}")
    
    # Create new files
    new_files = state["memory"].get("new_files", [])
    for file_path in new_files:
        # Ensure parent directory exists
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        
        # Create empty file if it doesn't exist
        if not os.path.exists(file_path):
            with open(file_path, "w") as f:
                f.write("")
            logger.info(f"Created new file: {file_path}")
            
            # Add new files to files_to_edit
            relative_path = os.path.relpath(file_path, state["repo_path"])
            if relative_path not in state["files_to_edit"]:
                state["files_to_edit"].append(relative_path)
                logger.info(f"Added new file to edit list: {relative_path}")
    
    # Add all files to forge context and prepare for review
    files_for_review = {}
    for file_path in state["files_to_edit"]:
        full_path = os.path.join(state["repo_path"], file_path)
        try:
            # Add to forge context
            forge.add_file(full_path)
            logger.info(f"Added file to forge context: {full_path}")
            
            # Read current content for review
            if os.path.exists(full_path):
                with open(full_path, 'r') as f:
                    files_for_review[file_path] = f.read()
            else:
                files_for_review[file_path] = ""  # Empty content for new files
                
        except Exception as e:
            logger.error(f"Error processing file {full_path}: {str(e)}")
    
    # Store files for review in state memory
    state["memory"]["files_for_review"] = files_for_review
    logger.info(f"Prepared {len(files_for_review)} files for review")
    
    return state

def create_planning_agent():
    """Create the planning workflow"""
    workflow = StateGraph(AgentState)

    # Add nodes
    workflow.add_node("get_query", get_user_query)
    workflow.add_node("ask_questions", ask_user_questions)
    workflow.add_node("identify_files", identify_files_to_edit)
    workflow.add_node("create_plan", create_implementation_plan)
    workflow.add_node("prepare_files", prepare_new_files)  # Add new node

    # Set entry point
    workflow.set_entry_point("get_query")

    # Add edges
    workflow.add_edge("get_query", "ask_questions")
    workflow.add_edge("ask_questions", "identify_files")
    workflow.add_edge("identify_files", "create_plan")
    workflow.add_edge("create_plan", "prepare_files")  # Add new edge
    workflow.add_edge("prepare_files", END)  # Update final edge

    return workflow.compile()

def start_planning(
    repo_path: str,
    codebase_overview: str,
    file_tree: str,
    file_descriptions: dict,
    forge=None,
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
        "memory": {
            "forge": forge
        },
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
    # print("\nImplementation Plan:")
    # print("=" * 80)
    # print(final_state["implementation_plan"])
    # print("=" * 80) 