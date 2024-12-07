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
from finalization_agent import start_finalization_agent
from utils.forge_interface import ForgeInterface
from utils.subprocess_handler import SubprocessHandler
import asyncio
import urllib3
import json

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
    print("4. Finalization (includes planning, execution, and validation)")
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
                'finalization': selected >= 4
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
        final_state = None
        
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
                            # Create and run a new event loop for each file
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
            
            # Start execution agent
            execution_state = start_execution_agent(planning_state, forge_interface)
        
        # Run validation if enabled
        if run_stages.get('validation'):
            if execution_state and execution_state.get('code_query_decision') == 'END':
                print("\nStarting validation phase...")
                validation_state = start_validation_agent(
                    planning_state['implementation_plan'],
                    forge_interface
                )
        
        # Run finalization if enabled
        if run_stages.get('finalization'):
            if validation_state and validation_state.get('validation_status') == 'END':
                print("\nStarting finalization phase...")
                final_state = start_finalization_agent(
                    planning_state['implementation_plan']
                )

        return {
            'setup_state': setup_state,
            'planning_state': planning_state,
            'execution_state': execution_state,
            'validation_state': validation_state,
            'finalization_state': final_state
        }
        
    except Exception as e:
        logging.error(f"Pipeline failed: {str(e)}")
        raise

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
            'finalization_state': None
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

