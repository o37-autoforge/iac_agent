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
llm = ChatOpenAI(model="gpt-4o", temperature=0, api_key=os.getenv("OPENAI_API_KEY"))

class ValidationState(TypedDict):
    messages: Annotated[List[BaseMessage], lambda existing, new: existing + [new]]
    memory: Dict[str, Any]
    implementation_plan: str
    repo_path: str
    current_command: Dict[str, str]
    command_output: str
    validation_status: str
    detected_issues: List[str]
    command_attempts: int
    max_attempts: int
    total_attempts: int  # Track total attempts across all commands
    max_total_attempts: int  # Maximum total attempts allowed

def check_command_runnable(state: ValidationState) -> ValidationState:
    """Check if the command is runnable or needs modification."""
    append_state_update(state["repo_path"], "validation", "check_command", f"Checking command: {state['current_command'].get('command', '')}")
    print("\n=== Checking Command Runnable ===")
    print(f"Current Command: {state['current_command']}")
    
    prompt = f"""
    As an Infrastructure as Code expert, analyze this command:
    {state['current_command']['command']}
    
    Purpose: {state['current_command']['purpose']}
    
    Check if the command:
    1. Contains any placeholders that need to be filled
    2. Uses correct syntax
    3. References valid files/resources
    4. Is ready to be executed as-is
    
    Return a JSON object:
    {{
        "is_runnable": boolean,
        "issues": ["list of issues if not runnable"],
        "user_input_needed": boolean,
        "user_prompt": "what to ask user if input needed",
        "modified_command": "suggested modification if needed"
    }}
    """
    
    class CommandCheck(BaseModel):
        is_runnable: bool
        issues: List[str]
        user_input_needed: bool
        user_prompt: str = ""
        modified_command: str = ""
    
    try:
        result = llm.with_structured_output(CommandCheck).invoke(prompt)
        print(f"LLM Check Result: {result}")
        
        if result.is_runnable:
            print("Command is runnable - proceeding to execute")
            state["validation_status"] = "execute"
        elif result.user_input_needed:
            print(f"User input needed: {result.user_prompt}")
            choice = input("Choose an option (1: Provide info, 2: Skip, 3: Modify): ").strip()
            if choice == "1":
                user_input = input(f"{result.user_prompt}: ").strip()
                state["current_command"]["command"] = result.modified_command.format(user_input=user_input)
                state["validation_status"] = "execute"
            elif choice == "2":
                logger.info("Skipping this validation step...")
                state["validation_status"] = "next"
            else:
                new_command = input("Enter the modified command: ").strip()
                state["current_command"]["command"] = new_command
                state["validation_status"] = "execute"
        else:
            print(f"Modifying command to: {result.modified_command}")
            state["current_command"]["command"] = result.modified_command
            state["validation_status"] = "execute"
            
    except Exception as e:
        print(f"ERROR in check_command_runnable: {str(e)}")
        logger.error(f"Error checking command: {e}")
        state["validation_status"] = "error"
    
    print(f"Validation status set to: {state['validation_status']}")
    return state

def execute_command(state: ValidationState) -> ValidationState:
    """Execute the current validation command."""
    append_state_update(state["repo_path"], "validation", "execute_command", f"Executing: {state['current_command'].get('command', '')}")
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
        # Clean the output before storing
        raw_output = result.stdout if result.returncode == 0 else result.stderr
        state["command_output"] = clean_ansi_escape_sequences(raw_output)
        print(f"Command output:\n{state['command_output']}")
        
        state["memory"].setdefault('executed_commands', []).append({
            'command': state["current_command"]["command"],
            'output': state["command_output"],  # Store cleaned output
            'status': 'success' if result.returncode == 0 else 'error',
            'working_dir': state["repo_path"]
        })
        
        state["validation_status"] = "review"
        
    except Exception as e:
        error_msg = clean_ansi_escape_sequences(str(e))  # Clean error message
        print(f"ERROR in execute_command: {error_msg}")
        logger.error(f"Command execution failed: {error_msg}")
        state["command_output"] = error_msg
        state["validation_status"] = "review"
    
    finally:
        os.chdir(original_dir)
        print(f"Restored directory to: {os.getcwd()}")
    
    return state

def review_output(state: ValidationState) -> ValidationState:
    """Review command output and determine next steps."""
    append_state_update(state["repo_path"], "validation", "review_output", "Reviewing command execution results")
    print("\n=== Reviewing Command Output ===")
    
    # Clean the output before checking
    clean_output = clean_ansi_escape_sequences(state["command_output"])
    clean_expected = clean_ansi_escape_sequences(state["current_command"]["expected_output"])
    
    success = clean_output and "error" not in clean_output.lower()
    matches_expected = clean_expected.lower() in clean_output.lower()
    has_warnings = "warning" in clean_output.lower()
    has_error = "error" in clean_output.lower()
    
    print(f"Success check: {success}")
    print(f"Expected output match: {matches_expected}")
    print(f"Has warnings: {has_warnings}")
    print(f"Has errors: {has_error}")
    print(f"Total attempts so far: {state['total_attempts']}/{state['max_total_attempts']}")
    
    # Check if we've exceeded total attempts
    if state["total_attempts"] >= state["max_total_attempts"]:
        print(f"\nMaximum total attempts ({state['max_total_attempts']}) reached across all commands.")
        print("Moving to next command...")
        state["validation_status"] = "next"
        return state
    
    # For terraform plan, don't try to fix output mismatches
    if "terraform plan" in state["current_command"]["command"]:
        if has_error:
            if state["command_attempts"] < state["max_attempts"]:
                state["command_attempts"] += 1
                state["total_attempts"] += 1
                state["memory"]["fix_query"] = f"Fix this Terraform error: {clean_output}"
                state["validation_status"] = "fix"
            else:
                state["validation_status"] = "next"
        else:
            # If plan succeeds without errors, consider it successful regardless of output
            print("Plan completed successfully - continuing...")
            state["validation_status"] = "next"
        return state
    
    # First try to automatically handle common errors
    if has_error:
        error_output = clean_output.lower()
        
        # Handle known error patterns automatically
        if "terraform init" in state["current_command"]["command"]:
            if "must use terraform init -upgrade" in error_output:
                print("Detected version mismatch - attempting automatic fix...")
                state["current_command"]["command"] = "terraform init -upgrade"
                state["validation_status"] = "execute"
                return state
                
            if "provider.aws: no suitable version installed" in error_output:
                print("Missing provider - attempting automatic fix...")
                state["current_command"]["command"] = "terraform init -upgrade"
                state["validation_status"] = "execute"
                return state
    
    # If we've reached max attempts for this command, move on
    if state["command_attempts"] >= state["max_attempts"]:
        print(f"\nMaximum attempts ({state['max_attempts']}) reached for current command.")
        print("Moving to next command...")
        state["validation_status"] = "next"
        return state
    
    # If everything is successful, move to next command
    if success and (matches_expected or "terraform plan" in state["current_command"]["command"]):
        print("Validation step passed successfully")
        state["validation_status"] = "next"
    else:
        # If not successful but still have attempts, try to fix
        print(f"Validation failed - Attempting fix (Attempt {state['command_attempts'] + 1}/{state['max_attempts']})")
        state["command_attempts"] += 1
        state["total_attempts"] += 1
        state["memory"]["fix_query"] = f"Fix this error: {clean_output}"
        state["validation_status"] = "fix"
    
    print(f"Setting validation status to: {state['validation_status']}")
    return state

def get_next_command(state: ValidationState) -> ValidationState:
    """Get the next command or end validation."""
    append_state_update(state["repo_path"], "validation", "get_next_command", "Getting next validation command")
    print("\n=== Getting Next Command ===")
    
    remaining_commands = len(state["memory"].get("commands", []))
    print(f"Remaining commands: {remaining_commands}")
    
    if not state["memory"].get("commands"):
        print("No more commands - ending validation")
        state["validation_status"] = "end"
    else:
        state["current_command"] = state["memory"]["commands"].pop(0)
        print(f"Next command: {state['current_command']}")
        state["command_attempts"] = 0
        state["validation_status"] = "check"
    
    print(f"Setting validation status to: {state['validation_status']}")
    return state

def clean_ansi_escape_sequences(text: str) -> str:
    """Remove ANSI escape sequences and other decorators from text."""
    # Remove ANSI escape sequences
    ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
    text = ansi_escape.sub('', text)
    
    # Remove other common decorators
    text = re.sub(r'\^?\[\d+m', '', text)  # Remove color codes
    text = re.sub(r'\^?\[\d+;\d+m', '', text)  # Remove complex color codes
    text = re.sub(r'\^?\[0m', '', text)  # Remove reset codes
    text = re.sub(r'\^?\[4m', '', text)  # Remove underline codes
    
    # Clean up any remaining control characters
    text = ''.join(char for char in text if ord(char) >= 32 or char in '\n\r\t')
    
    return text.strip()

def apply_fix(state: ValidationState) -> ValidationState:
    """Apply fixes using the forge wrapper."""
    append_state_update(state["repo_path"], "validation", "apply_fix", "Applying fixes to validation issues")
    print("\n=== Applying Fix ===")
    
    forge_wrapper = state["memory"].get("forge_wrapper")
    if not forge_wrapper:
        print("ERROR: No forge wrapper available")
        state["validation_status"] = "next"
        return state
    
    try:
        fix_query = state["memory"].get("fix_query")
        if not fix_query:
            print("ERROR: No fix query available")
            state["validation_status"] = "next"
            return state
            
        # Clean the fix query before sending to forge
        clean_query = clean_ansi_escape_sequences(fix_query)
        print(f"Sending fix query to forge: {clean_query}")
        
        # Use ForgeWrapper to apply changes
        result = forge_wrapper.chat(clean_query)
        
        # Check if we got an EditResult
        if hasattr(result, 'files_changed'):
            print(f"Files changed: {', '.join(result.files_changed)}")
            if result.diff:
                print(f"Changes made:\n{result.diff}")
            clean_output = f"Changed files: {', '.join(result.files_changed)}"
        else:
            clean_output = clean_ansi_escape_sequences(str(result))
            
        print(f"Fix applied through forge. Output: {clean_output}")
        
        state["memory"].setdefault('fix_attempts', []).append({
            'query': clean_query,
            'output': clean_output,
            'timestamp': datetime.now().isoformat()
        })
        
        state["validation_status"] = "execute"
        
    except Exception as e:
        print(f"ERROR in apply_fix: {str(e)}")
        logger.error(f"Error applying fix: {e}")
        state["validation_status"] = "next"
    
    print(f"Setting validation status to: {state['validation_status']}")
    return state

def request_user_approval(state: ValidationState) -> ValidationState:
    """Request user approval of validation results."""
    append_state_update(state["repo_path"], "validation", "request_user_approval", "Requesting user approval for validation")
    print("\n=== Validation Results Summary ===")
    print("\nExecuted Commands and Results:")
    
    all_successful = True
    for cmd in state["memory"]["executed_commands"]:
        print(f"\nCommand: {cmd['command']}")
        print(f"Status: {cmd['status']}")
        print(f"Output:\n{cmd['output']}")
        if cmd['status'] != 'success':
            all_successful = False
    
    if not all_successful:
        print("\nWarning: Some commands did not complete successfully.")
    
    print("\nWould you like to:")
    print("1: Approve and continue")
    print("2: Fix issues and revalidate")
    print("3: Exit pipeline")
    
    choice = input("Enter your choice (1-3): ").strip()
    
    if choice == "1":
        print("Validation approved - continuing pipeline")
        state["validation_status"] = "end"
    elif choice == "2":
        print("\nWhat would you like to fix?")
        print("1: Specific issue")
        print("2: General improvement")
        fix_choice = input("Enter choice (1-2): ").strip()
        
        if fix_choice == "1":
            print("\nAvailable commands:")
            for i, cmd in enumerate(state["memory"]["executed_commands"], 1):
                print(f"{i}. {cmd['command']}")
            cmd_idx = int(input("Enter command number to fix: ").strip()) - 1
            cmd = state["memory"]["executed_commands"][cmd_idx]
            
            print(f"\nCommand output:\n{cmd['output']}")
            fix_description = input("Describe what needs to be fixed: ").strip()
            
            # Build fix query
            fix_query = f"""
            Fix the following issue with the Terraform configuration:
            Command: {cmd['command']}
            Output: {cmd['output']}
            Issue to fix: {fix_description}
            """
        else:
            fix_query = input("Describe the improvements needed: ").strip()
        
        # Send to forge wrapper
        try:
            forge_wrapper = state["memory"].get("forge_wrapper")
            if forge_wrapper:
                print("Sending fix query to forge...")
                result = forge_wrapper.chat(fix_query)
                
                # Check if we got an EditResult
                if hasattr(result, 'files_changed'):
                    print(f"Files changed: {', '.join(result.files_changed)}")
                    if result.diff:
                        print(f"Changes made:\n{result.diff}")
                    output = f"Changed files: {', '.join(result.files_changed)}"
                else:
                    output = str(result)
                    
                print(f"Fix applied: {output}")
                
                # Reset validation state
                state["command_attempts"] = 0
                state["total_attempts"] = 0
                state["validation_status"] = "next"
                # Preserve the original commands list for revalidation
                state["memory"]["commands"] = [cmd.dict() for cmd in state["memory"]["original_commands"]]
            else:
                print("ERROR: No forge wrapper available")
                state["validation_status"] = "end"
        except Exception as e:
            print(f"ERROR applying fix: {e}")
            state["validation_status"] = "end"
    else:
        print("Validation rejected - exiting pipeline")
        state["validation_status"] = "end"
    
    return state

def create_validation_graph():
    """Create the validation workflow graph."""
    workflow = StateGraph(ValidationState)
    
    workflow.add_node("check", check_command_runnable)
    workflow.add_node("execute", execute_command)
    workflow.add_node("review", review_output)
    workflow.add_node("next", get_next_command)
    workflow.add_node("fix", apply_fix)
    workflow.add_node("approve", request_user_approval)
    
    workflow.set_entry_point("next")
    
    workflow.add_conditional_edges(
        "check",
        lambda x: x["validation_status"],
        {
            "execute": "execute",
            "next": "next"
        }
    )
    
    workflow.add_conditional_edges(
        "execute",
        lambda x: x["validation_status"],
        {
            "review": "review"
        }
    )
    
    workflow.add_conditional_edges(
        "review",
        lambda x: x["validation_status"],
        {
            "next": "next",
            "fix": "fix",
            "end": END
        }
    )
    
    workflow.add_edge("fix", "execute")
    workflow.add_conditional_edges(
        "next",
        lambda x: x["validation_status"],
        {
            "check": "check",
            "end": "approve"  # Go to approval instead of ending
        }
    )
    
    # Add approval node edges
    workflow.add_conditional_edges(
        "approve",
        lambda x: x["validation_status"],
        {
            "next": "next",  # For revalidation
            "end": END
        }
    )
    
    return workflow.compile()

def start_validation_agent(
    implementation_plan: str,
    repo_path: str,
    forge=None,
    execution_state=None,
    max_attempts: int = 3,
    max_total_attempts: int = 15
) -> dict:
    """Start the validation agent."""
    print("\n=== Starting Validation Agent ===")
    print(f"Repo path: {repo_path}")
    print(f"Implementation plan:\n{implementation_plan}")
    
    commands_prompt = f"""
    Generate a list of commands to validate this Terraform implementation WITHOUT making any changes to infrastructure.
    Focus only on validation, linting, and plan commands.
    DO NOT include any apply, destroy, or infrastructure-modifying commands.
    DO NOT include commands that try to query existing infrastructure (as it doesn't exist yet).
    
    Include commands for:
    1. Terraform initialization and validation
    2. Configuration syntax checking
    3. Plan generation and review
    4. Security scanning (if applicable)
    5. Policy compliance checking (if applicable)
    
    {implementation_plan}
    
    Return a JSON array of commands:
    [
        {{
            "command": "exact command to run",
            "purpose": "what this validates",
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
    
    print("\nGenerating validation commands...")
    commands = llm.with_structured_output(CommandList).invoke(commands_prompt)
    print("\nProposed validation commands:")
    for i, cmd in enumerate(commands.commands, 1):
        # Clean any potential formatting in the command details
        clean_cmd = clean_ansi_escape_sequences(cmd.command)
        clean_purpose = clean_ansi_escape_sequences(cmd.purpose)
        clean_output = clean_ansi_escape_sequences(cmd.expected_output)
        
        print(f"\n{i}. Command: {clean_cmd}")
        print(f"   Purpose: {clean_purpose}")
        print(f"   Expected Output: {clean_output}")
    
    print("\nWould you like to:")
    print("1: Proceed with these commands")
    print("2: Modify commands")
    print("3: Add new commands")
    choice = input("Enter your choice (1-3): ").strip()
    
    if choice == "2":
        for i, cmd in enumerate(commands.commands):
            print(f"\nCommand {i+1}: {cmd.command}")
            if input("Modify this command? (y/n): ").lower() == 'y':
                cmd.command = input("Enter new command: ").strip()
                cmd.purpose = input("Enter purpose: ").strip()
                cmd.expected_output = input("Enter expected output: ").strip()
    elif choice == "3":
        while input("\nAdd new command? (y/n): ").lower() == 'y':
            new_cmd = Command(
                command=input("Enter command: ").strip(),
                purpose=input("Enter purpose: ").strip(),
                expected_output=input("Enter expected output: ").strip()
            )
            commands.commands.append(new_cmd)
    
    initial_state = ValidationState(
        messages=[SystemMessage(content="Starting validation process")],
        memory={
            "commands": [cmd.dict() for cmd in commands.commands],
            "original_commands": commands.commands,  # Store original commands for revalidation
            "executed_commands": [],
            "forge": forge
        },
        implementation_plan=implementation_plan,
        repo_path=repo_path,
        current_command={},
        command_output="",
        validation_status="",
        detected_issues=[],
        command_attempts=0,
        max_attempts=max_attempts,
        total_attempts=0,
        max_total_attempts=max_total_attempts
    )
    
    print("\nCreating validation workflow...")
    workflow = create_validation_graph()
    
    print("\nStarting validation workflow...")
    final_state = workflow.invoke(initial_state)
    
    print("\n=== Validation Complete ===")
    print(f"Final validation status: {final_state['validation_status']}")
    
    return final_state 