from langgraph.graph import StateGraph, END
from typing import TypedDict, Annotated, List, Dict, Any, Optional
from langchain_core.messages import BaseMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel
from datetime import datetime
import logging
import os
import subprocess
import json
from utils.state_utils import append_state_update
from utils.rag_utils import RAGUtils
from dotenv import load_dotenv
import asyncio
import re

# Load environment variables
load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
llm = ChatOpenAI(model="gpt-4", temperature=0, api_key=os.getenv("OPENAI_API_KEY"))

class ApplicationState(TypedDict):
    messages: Annotated[List[BaseMessage], lambda existing, new: existing + [new]]
    memory: Dict[str, Any]
    implementation_plan: str
    repo_path: str
    current_command: Dict[str, str]
    command_output: str
    application_status: str
    detected_issues: List[str]
    command_attempts: int
    max_attempts: int
    total_attempts: int
    max_total_attempts: int

def clean_ansi_escape_sequences(text: str) -> str:
    """Remove ANSI escape sequences and other decorators from text."""
    ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
    text = ansi_escape.sub('', text)
    text = re.sub(r'\^?\[\d+m', '', text)
    text = re.sub(r'\^?\[\d+;\d+m', '', text)
    text = re.sub(r'\^?\[0m', '', text)
    text = re.sub(r'\^?\[4m', '', text)
    text = ''.join(char for char in text if ord(char) >= 32 or char in '\n\r\t')
    return text.strip()

def check_command_safety(state: ApplicationState) -> ApplicationState:
    """Check if the command is safe to execute."""
    print("\n=== Checking Command Safety ===")
    print(f"Current Command: {state['current_command']}")
    
    prompt = f"""
    As an Infrastructure as Code expert, analyze this command for safety:
    {state['current_command']['command']}
    
    Purpose: {state['current_command']['purpose']}
    
    Check if the command:
    1. Will make the intended changes safely
    2. Has proper error handling
    3. Can be rolled back if needed
    4. Might cause service disruption
    5. Requires additional precautions
    
    Return a JSON object:
    {{
        "is_safe": boolean,
        "risks": ["list of potential risks"],
        "precautions_needed": boolean,
        "precaution_steps": ["list of precautions if needed"],
        "modified_command": "suggested safer version if needed"
    }}
    """
    
    class SafetyCheck(BaseModel):
        is_safe: bool
        risks: List[str]
        precautions_needed: bool
        precaution_steps: List[str]
        modified_command: str = ""
    
    try:
        result = llm.with_structured_output(SafetyCheck).invoke(prompt)
        print(f"Safety Check Result: {result}")
        
        if not result.is_safe:
            print("\nCommand has potential risks:")
            for risk in result.risks:
                print(f"- {risk}")
            
            if result.precautions_needed:
                print("\nRequired precautions:")
                for step in result.precaution_steps:
                    print(f"- {step}")
            
            print("\nWould you like to:")
            print("1: Proceed anyway")
            print("2: Use modified safer command")
            print("3: Skip this command")
            choice = input("Enter choice (1-3): ").strip()
            
            if choice == "2" and result.modified_command:
                state["current_command"]["command"] = result.modified_command
                state["application_status"] = "execute"
            elif choice == "3":
                state["application_status"] = "next"
                return state
        
        state["application_status"] = "execute"
        
    except Exception as e:
        print(f"ERROR in check_command_safety: {str(e)}")
        logger.error(f"Error checking command: {e}")
        state["application_status"] = "error"
    
    return state

def execute_command(state: ApplicationState) -> ApplicationState:
    """Execute the infrastructure change command."""
    print("\n=== Executing Command ===")
    print(f"Working Directory: {state['repo_path']}")
    print(f"Command to execute: {state['current_command']['command']}")
    
    original_dir = os.getcwd()
    
    try:
        os.chdir(state["repo_path"])
        print(f"Changed to directory: {os.getcwd()}")
        
        result = subprocess.run(
            state["current_command"]["command"],
            shell=True,
            capture_output=True,
            text=True
        )
        
        print(f"Command return code: {result.returncode}")
        raw_output = result.stdout if result.returncode == 0 else result.stderr
        state["command_output"] = clean_ansi_escape_sequences(raw_output)
        print(f"Command output:\n{state['command_output']}")
        
        state["memory"].setdefault('executed_commands', []).append({
            'command': state["current_command"]["command"],
            'output': state["command_output"],
            'status': 'success' if result.returncode == 0 else 'error',
            'working_dir': state["repo_path"]
        })
        
        state["application_status"] = "verify"
        
    except Exception as e:
        error_msg = clean_ansi_escape_sequences(str(e))
        print(f"ERROR in execute_command: {error_msg}")
        logger.error(f"Command execution failed: {error_msg}")
        state["command_output"] = error_msg
        state["application_status"] = "verify"
    
    finally:
        os.chdir(original_dir)
        print(f"Restored directory to: {os.getcwd()}")
    
    return state

def verify_changes(state: ApplicationState) -> ApplicationState:
    """Verify that the changes were applied correctly."""
    print("\n=== Verifying Changes ===")
    
    clean_output = clean_ansi_escape_sequences(state["command_output"])
    clean_expected = clean_ansi_escape_sequences(state["current_command"]["expected_output"])
    
    success = clean_output and "error" not in clean_output.lower()
    matches_expected = clean_expected.lower() in clean_output.lower()
    has_warnings = "warning" in clean_output.lower()
    
    # If we've reached max attempts for this command, give user options
    if state["command_attempts"] >= state["max_attempts"]:
        print(f"\nMaximum attempts ({state['max_attempts']}) reached for current command.")
        print(f"Command: {state['current_command']['command']}")
        print(f"Current output: {clean_output}")
        print("\nOptions:")
        print("1: Skip remaining commands and end application")
        print("2: Skip this command and move to next")
        print("3: Edit command manually and continue")
        
        while True:
            choice = input("Enter your choice (1-3): ").strip()
            if choice == "1":
                print("Skipping remaining commands...")
                state["application_status"] = "end"
                return state
            elif choice == "2":
                print("Skipping to next command...")
                state["application_status"] = "next"
                return state
            elif choice == "3":
                print("\nCurrent command:")
                print(state["current_command"]["command"])
                new_command = input("Enter modified command (or press Enter to keep current): ").strip()
                if new_command:
                    state["current_command"]["command"] = new_command
                    state["command_attempts"] = 0  # Reset attempts for the modified command
                    state["application_status"] = "execute"
                    return state
            else:
                print("Please enter 1, 2, or 3")
    
    if success and matches_expected:
        print("Changes applied successfully")
        state["application_status"] = "next"
    else:
        print(f"Verification failed - Attempting fix (Attempt {state['command_attempts'] + 1}/{state['max_attempts']})")
        state["command_attempts"] += 1
        state["total_attempts"] += 1
        state["memory"]["fix_query"] = f"Fix this error: {clean_output}"
        state["application_status"] = "rollback"
    
    return state

def get_next_command(state: ApplicationState) -> ApplicationState:
    """Get the next command or end application."""
    print("\n=== Getting Next Command ===")
    
    if not state["memory"].get("commands"):
        print("No more commands - ending application")
        state["application_status"] = "end"
    else:
        state["current_command"] = state["memory"]["commands"].pop(0)
        print(f"Next command: {state['current_command']}")
        state["command_attempts"] = 0
        state["application_status"] = "check"
    
    return state

def rollback_changes(state: ApplicationState) -> ApplicationState:
    """Rollback the last applied changes."""
    print("\n=== Rolling Back Changes ===")
    
    if not state["memory"].get("executed_commands"):
        print("No changes to rollback")
        state["application_status"] = "end"
        return state
    
    last_command = state["memory"]["executed_commands"][-1]
    print(f"Rolling back command: {last_command['command']}")
    
    # Generate rollback command based on the original command
    rollback_prompt = f"""
    Generate a rollback command for this Terraform command:
    {last_command['command']}
    
    Return just the rollback command string.
    """
    
    try:
        rollback_command = llm.invoke(rollback_prompt).content.strip()
        print(f"Executing rollback: {rollback_command}")
        
        result = subprocess.run(
            rollback_command,
            shell=True,
            capture_output=True,
            text=True,
            cwd=state["repo_path"]
        )
        
        if result.returncode == 0:
            print("Rollback successful")
        else:
            print(f"Rollback failed: {result.stderr}")
        
    except Exception as e:
        print(f"ERROR during rollback: {str(e)}")
    
    state["application_status"] = "end"
    return state

def create_application_graph():
    """Create the application workflow graph."""
    workflow = StateGraph(ApplicationState)
    
    workflow.add_node("check", check_command_safety)
    workflow.add_node("execute", execute_command)
    workflow.add_node("verify", verify_changes)
    workflow.add_node("next", get_next_command)
    workflow.add_node("rollback", rollback_changes)
    
    workflow.set_entry_point("next")
    
    workflow.add_conditional_edges(
        "check",
        lambda x: x["application_status"],
        {
            "execute": "execute",
            "next": "next"
        }
    )
    
    workflow.add_conditional_edges(
        "execute",
        lambda x: x["application_status"],
        {
            "verify": "verify"
        }
    )
    
    workflow.add_conditional_edges(
        "verify",
        lambda x: x["application_status"],
        {
            "next": "next",
            "execute": "execute",
            "rollback": "rollback",
            "end": END
        }
    )
    
    workflow.add_conditional_edges(
        "next",
        lambda x: x["application_status"],
        {
            "check": "check",
            "end": END
        }
    )
    
    workflow.add_edge("rollback", END)
    
    return workflow.compile()

def start_application_agent(
    implementation_plan: str,
    repo_path: str,
    forge_interface=None,
    rag_utils: Optional[RAGUtils] = None,
    max_attempts: int = 3,
    max_total_attempts: int = 15
) -> dict:
    """Start the application agent."""
    print("\n=== Starting Application Agent ===")
    
    # Create planning directory if it doesn't exist
    planning_dir = os.path.join(repo_path, "planning")
    os.makedirs(planning_dir, exist_ok=True)
    
    # Path for application commands JSON
    application_commands_path = os.path.join(planning_dir, "application_commands.json")
    
    commands_prompt = f"""
    Generate a list of commands to apply the Terraform changes.
    Include only commands that will modify the infrastructure.
    Include proper error handling and verification steps.
    
    {implementation_plan}
    
    Return a JSON array of commands:
    [
        {{
            "command": "exact command to run",
            "purpose": "what this command does",
            "expected_output": "what success looks like"
        }}
    ]
    """
    
    class Command(BaseModel):
        command: str
        purpose: str
        expected_output: str
    
    class CommandList(BaseModel):
        commands: List[Command]
    
    print("\nGenerating application commands...")
    commands = llm.with_structured_output(CommandList).invoke(commands_prompt)
    
    # Convert commands to JSON and save to file
    commands_json = json.dumps([{
        "command": cmd.command,
        "purpose": cmd.purpose,
        "expected_output": cmd.expected_output
    } for cmd in commands.commands], indent=2)
    
    with open(application_commands_path, 'w') as f:
        f.write(commands_json)
    
    print(f"\nApplication commands have been written to: {application_commands_path}")
    print("You can now review and modify the commands if needed.")
    print("Press 'y' when you're done editing, or 'n' to use as-is.")
    
    while True:
        choice = input("Would you like to edit the commands? (y/n): ").lower()
        if choice == 'y':
            input("Edit the file and press Enter when done...")
            try:
                # Read potentially modified commands
                with open(application_commands_path, 'r') as f:
                    edited_json = json.load(f)
                # Validate the structure
                commands = CommandList(commands=[Command(**cmd) for cmd in edited_json])
                print("\nApplication commands updated successfully!")
                break
            except Exception as e:
                print(f"\nError in JSON format: {str(e)}")
                print("Please fix the JSON format and try again.")
        elif choice == 'n':
            break
        else:
            print("Please enter 'y' or 'n'")
    
    initial_state = ApplicationState(
        messages=[SystemMessage(content="Starting application process")],
        memory={
            "commands": [cmd.dict() for cmd in commands.commands],
            "original_commands": commands.commands,
            "executed_commands": [],
            "forge_interface": forge_interface,
            "rag_utils": rag_utils,
            "application_commands_path": application_commands_path
        },
        implementation_plan=implementation_plan,
        repo_path=repo_path,
        current_command={},
        command_output="",
        application_status="",
        detected_issues=[],
        command_attempts=0,
        max_attempts=max_attempts,
        total_attempts=0,
        max_total_attempts=max_total_attempts
    )
    
    workflow = create_application_graph()
    final_state = workflow.invoke(initial_state, config=config)
    
    return final_state 