import os
import time
import logging
import subprocess
from pathlib import Path
from dotenv import load_dotenv
import importlib.util
import sys
from continuous_setup import start_continuous_setup
from planning_agent import start_planning
from execution_agent import start_execution_agent
from validation_agent import start_validation_agent
from utils.forge_interface import ForgeInterface
from utils.subprocess_handler import SubprocessHandler
import asyncio
import urllib3
import json
from utils.rag_utils import RAGUtils
from application_agent import start_application_agent
from typing import Optional
import requests  # Add this import for GitHub API
from datetime import datetime
from github import Github
from github.GithubException import GithubException

# Disable HTTP request logging
urllib3.disable_warnings()
logging.getLogger("openai").setLevel(logging.ERROR)
logging.getLogger("httpx").setLevel(logging.ERROR)
logging.getLogger("httpcore").setLevel(logging.ERROR)

# Configure only error logging
logging.basicConfig(
    level=logging.ERROR,
    format='%(asctime)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

def get_user_pipeline_config():
    """Get user's desired pipeline configuration and include prerequisite stages"""
    print("\nWhich pipeline stage would you like to run up to?")
    print("1. Planning")
    print("2. Execution (includes planning)")
    print("3. Validation (includes planning and execution)")
    print("4. Application (includes all previous stages)")
    print("\nEnter the number of the final stage you want to run (1-4)")
    
    while True:
        try:
            user_input = input("> ").strip()
            selected = int(user_input)
            
            if selected < 1 or selected > 4:
                print("Please enter a valid stage number (1-4)")
                continue
            
            # Include all prerequisite stages
            return {
                'planning': selected >= 1,
                'execution': selected >= 2,
                'validation': selected >= 3,
                'application': selected >= 4
            }
        except ValueError:
            print("Please enter a single number (1-4)")

def orchestrate_pipeline(repo_path: str, run_stages: dict = None, persistent_states: dict = None):
    """
    Orchestrates the pipeline with configurable stages.
    """
    try:
        # Initialize states from persistent states if available
        setup_state = persistent_states.get('setup_state') if persistent_states else None
        planning_state = persistent_states.get('planning_state') if persistent_states else None
        execution_state = persistent_states.get('execution_state') if persistent_states else None
        validation_state = persistent_states.get('validation_state') if persistent_states else None
        application_state = None
        
        # Get planning file path from environment
        planning_file_path = os.getenv('PLANNING_FILE_PATH')
        if not planning_file_path:
            # Fallback to default path
            planning_file_path = os.path.join(repo_path, "planning", "implementation_plan.txt")
            
        # Ensure planning directory exists
        os.makedirs(os.path.dirname(planning_file_path), exist_ok=True)
        
        # Get handlers from setup state
        if setup_state:
            subprocess_handler = setup_state['subprocess_handler']
            forge_interface = setup_state['forge_interface']
        
        # Run planning if enabled
        if run_stages.get('planning'):
            print("\nStarting planning phase...")
            if not setup_state:
                raise ValueError("Setup state required for planning")
            planning_state = start_planning(
                repo_path=repo_path,
                codebase_overview=setup_state.get('codebase_overview', ''),
                file_tree=setup_state.get('file_tree', ''),
                file_descriptions=setup_state.get('file_descriptions', {}),
                subprocess_handler=subprocess_handler,
                forge_interface=forge_interface,
                planning_file_path=planning_file_path  # Pass the planning file path
            )
            # Add repo_path to planning state
            planning_state['repo_path'] = repo_path
            planning_state['planning_file_path'] = planning_file_path  # Store the path
        
        # Run execution if enabled
        if run_stages.get('execution'):
            if not planning_state:
                raise ValueError("Planning state required for execution")
            print("\nStarting execution phase...")
            
            # Initialize RAG utils
            rag_utils = RAGUtils(repo_path) if os.path.exists(repo_path) else None
            
            # Ensure forge is started with clean context
            subprocess_handler.start_forge(os.getenv("OPENAI_API_KEY"), [])
            
            # Get planning file path from state or environment
            planning_file_path = planning_state.get('planning_file_path') or os.getenv('PLANNING_FILE_PATH')
            if not planning_file_path:
                planning_file_path = os.path.join(repo_path, "planning", "implementation_plan.txt")
            
            # Read implementation plan from file
            if not os.path.exists(planning_file_path):
                raise ValueError(f"Implementation plan file not found at: {planning_file_path}")
            
            with open(planning_file_path, "r") as f:
                planning_state["implementation_plan"] = f.read()
            
            # Add files to forge context one at a time
            if planning_state.get("files_to_edit"):
                print("\nAdding files to forge context...")
                
                for file_path in planning_state["files_to_edit"]:
                    full_path = os.path.join(repo_path, file_path)
                    if os.path.exists(full_path):
                        try:
                            loop = asyncio.new_event_loop()
                            asyncio.set_event_loop(loop)
                            try:
                                result = loop.run_until_complete(
                                    forge_interface.add_file_to_context(file_path)
                                )
                                if result:
                                    print(f"Successfully added {file_path} to forge context")
                                else:
                                    print(f"Failed to add {file_path} to forge context")
                            finally:
                                loop.close()
                        except Exception as e:
                            print(f"Error adding {file_path} to forge context: {str(e)}")
            
            # Ensure repo_path is in planning state
            planning_state['repo_path'] = repo_path
            
            # Start execution agent with RAG utils
            execution_state = start_execution_agent(
                planning_state=planning_state,
                forge_interface=forge_interface,
                rag_utils=rag_utils
            )
        
        # Run validation if enabled
        if run_stages.get('validation'):
            if execution_state and execution_state.get('code_query_decision') == 'END':
                print("\nStarting validation phase...")
                validation_state = start_validation_agent(
                    implementation_plan=planning_state['implementation_plan'],
                    forge_interface=forge_interface,
                    repo_path=repo_path,
                    execution_state=execution_state
                )
        
        # Run application if enabled (replaces finalization)
        if run_stages.get('application'):
            if validation_state and validation_state.get('validation_status') == 'end':
                print("\nStarting application phase...")
                application_state = start_application_agent(
                    implementation_plan=planning_state['implementation_plan'],
                    forge_interface=forge_interface,
                    repo_path=repo_path,
                    rag_utils=rag_utils
                )
                
                # Store application state
                persistent_states['application_state'] = application_state
            else:
                print("\nValidation not completed successfully. Skipping application phase.")

        # After all stages are complete, handle git operations
        if any(run_stages.values()):  # If any stage was run
            print("\nWould you like to commit and push the changes? (y/n)")
            if input().lower() == 'y':
                git_result = handle_git_operations(repo_path)
                if git_result["status"] == "success":
                    print(f"\nChanges committed and pushed to branch: {git_result['branch']}")
                elif git_result["status"] == "no_changes":
                    print("\nNo changes to commit.")
                else:
                    print(f"\nError handling git operations: {git_result.get('error', 'Unknown error')}")

        return {
            'setup_state': setup_state,
            'planning_state': planning_state,
            'execution_state': execution_state,
            'validation_state': validation_state,
            'application_state': application_state,
            'git_state': git_result if 'git_result' in locals() else None
        }
        
    except Exception as e:
        logging.error(f"Pipeline failed: {str(e)}")
        raise

def handle_git_operations(repo_path: str, branch_name: Optional[str] = None) -> dict:
    """Handle git operations after pipeline completion."""
    print("\n=== Handling Git Operations ===")
    
    try:
        # Get default branch name if not provided
        if not branch_name:
            branch_name = f"iac-agent-changes-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        
        # Check if we're in a git repository
        subprocess.run(["git", "rev-parse", "--git-dir"], cwd=repo_path, check=True, capture_output=True)
        
        # Get current branch
        current_branch = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=repo_path,
            check=True,
            capture_output=True,
            text=True
        ).stdout.strip()
        
        # Check if branch already exists
        existing_branch = subprocess.run(
            ["git", "branch", "--list", branch_name],
            cwd=repo_path,
            capture_output=True,
            text=True
        ).stdout.strip()
        
        if existing_branch:
            print(f"\nBranch {branch_name} already exists.")
            print("Would you like to:")
            print("1: Create new branch with timestamp")
            print("2: Use existing branch")
            print("3: Delete existing branch and create new")
            choice = input("Enter choice (1-3): ").strip()
            
            if choice == "1":
                branch_name = f"iac-agent-changes-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
            elif choice == "3":
                subprocess.run(["git", "branch", "-D", branch_name], cwd=repo_path, check=True)
        
        # Create and checkout new branch if needed
        if not existing_branch or choice in ["1", "3"]:
            subprocess.run(["git", "checkout", "-b", branch_name], cwd=repo_path, check=True)
        elif existing_branch:
            subprocess.run(["git", "checkout", branch_name], cwd=repo_path, check=True)
        
        # Stage all changes
        subprocess.run(["git", "add", "-A"], cwd=repo_path, check=True)
        
        # Check if there are changes to commit
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=repo_path,
            capture_output=True,
            text=True
        ).stdout.strip()
        
        if not status:
            print("No changes to commit.")
            return {"status": "no_changes"}
        
        # Create commit
        commit_msg = "Infrastructure changes by Forge\n\nAutomated changes made by the Forge IaC Agent."
        subprocess.run(["git", "commit", "-m", commit_msg], cwd=repo_path, check=True)
        
        # Get remote URL and push
        remote_url = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=repo_path,
            capture_output=True,
            text=True
        ).stdout.strip()
        
        if not remote_url:
            print("No remote repository configured.")
            return {"status": "no_remote"}
        
        subprocess.run(["git", "push", "--set-upstream", "origin", branch_name], cwd=repo_path, check=True)
        
        # Handle GitHub PR using PyGithub
        if "github.com" in remote_url:
            github_token = os.getenv("GITHUB_TOKEN")
            if github_token:
                try:
                    # Initialize GitHub client
                    g = Github(github_token)
                    
                    # Extract repo info from remote URL
                    repo_info = remote_url.split("github.com/")[-1].replace(".git", "").split("/")
                    repo = g.get_repo(f"{repo_info[0]}/{repo_info[1]}")
                    
                    # Get list of changes for PR body
                    changes_list = status.split('\n')
                    formatted_changes = '\n'.join([f"- {change}" for change in changes_list])
                    
                    pr_body = f"""
                    Infrastructure changes made by IAC Agent

                    Changes include:
                    {formatted_changes}

                    Please review the changes carefully before merging.
                    """
                    
                    # Check for existing PR
                    existing_prs = list(repo.get_pulls(
                        state='open',
                        head=f"{repo_info[0]}:{branch_name}"
                    ))
                    
                    if existing_prs:
                        # Update existing PR
                        pr = existing_prs[0]
                        pr.edit(body=pr_body)
                        print(f"Updated existing PR #{pr.number}")
                    else:
                        # Create new PR
                        pr = repo.create_pull(
                            title="Infrastructure Changes by IAC Agent",
                            body=pr_body,
                            head=branch_name,
                            base=current_branch
                        )
                        print(f"Created new PR #{pr.number}")
                        
                except GithubException as e:
                    print(f"GitHub API error: {e}")
                    return {"status": "error", "error": str(e)}
        
        return {
            "status": "success",
            "branch": branch_name,
            "commit": subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=repo_path,
                capture_output=True,
                text=True
            ).stdout.strip()
        }
        
    except subprocess.CalledProcessError as e:
        print(f"Git operation failed: {e}")
        return {"status": "error", "error": str(e)}
    except Exception as e:
        print(f"Error during git operations: {e}")
        return {"status": "error", "error": str(e)}

def main():
    load_dotenv()
    repo_path = os.getenv("REPO_PATH")
    if not repo_path:
        raise ValueError("REPO_PATH not found in .env file")
    
    try:
        # First run continuous setup
        print("\nStarting continuous setup...")
        setup_state = start_continuous_setup(repo_path)
        subprocess_handler = setup_state['subprocess_handler']
        forge_interface = setup_state['forge_interface']
        
        print("\nContinuous setup complete. Starting interactive pipeline mode.")
        
        # Store states that need to persist between iterations
        persistent_states = {
            'setup_state': setup_state,
            'planning_state': None,
            'execution_state': None,
            'validation_state': None,
            'application_state': None
        }
        
        # Run pipeline stages in a loop
        while True:
            try:
                # Get user's desired pipeline configuration
                run_stages = get_user_pipeline_config()
                
                # Add setup state to run_stages
                run_stages['setup'] = False  # Setup already done
                
                print("\nStarting pipeline with selected stages...")
                                
                # Run the pipeline with current stages and persistent states
                final_state = orchestrate_pipeline(
                    repo_path=repo_path,
                    run_stages=run_stages,
                    persistent_states=persistent_states
                )
                
                # Update persistent states with any new states
                for key in persistent_states:
                    if key in final_state and final_state[key] is not None:
                        persistent_states[key] = final_state[key]
                
                print("\nPipeline execution complete.")
                print("=" * 80)
                
                # Ask if user wants to run another iteration
                while True:
                    continue_input = input("\nWould you like to run another pipeline iteration? (y/n): ").lower()
                    if continue_input in ['y', 'n']:
                        break
                    print("Please enter 'y' or 'n'")
                
                if continue_input == 'n':
                    print("\nExiting pipeline...")
                    break
                    
            except KeyboardInterrupt:
                print("\nPipeline iteration interrupted by user.")
                if input("\nWould you like to exit? (y/n): ").lower() == 'y':
                    break
            except Exception as e:
                print(f"\nError during pipeline iteration: {str(e)}")
                if input("\nWould you like to try another iteration? (y/n): ").lower() == 'n':
                    break
        
        # Cleanup
        if setup_state and setup_state.get('observer'):
            setup_state['observer'].stop()
        if setup_state and setup_state.get('subprocess_handler'):
            setup_state['subprocess_handler'].close_forge()
            
    except Exception as e:
        logging.error(f"Error during pipeline orchestration: {str(e)}")
        raise

if __name__ == "__main__":
    main()

