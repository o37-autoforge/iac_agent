from langgraph.graph import StateGraph, END
from typing import TypedDict, Annotated, List, Dict, Any, Literal
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, SystemMessage
from langchain_openai import ChatOpenAI
from execution_agent import code_executor
from pydantic import BaseModel
from datetime import datetime
import logging
import os
import asyncio
import json
from pathlib import Path
from utils.rag_utils import RAGUtils
from utils.state_utils import append_state_update
from typing import Optional

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)
llm = ChatOpenAI(model="gpt-4o", temperature=0)

def manage_messages(existing: List[BaseMessage], new_message: BaseMessage) -> List[BaseMessage]:
    """Message management reducer function"""
    return existing + [new_message]

class ExecutionState(TypedDict):
    messages: Annotated[List[BaseMessage], manage_messages]
    memory: Dict[str, Any]
    implementation_plan: str
    repo_path: str
    code_execution_output: str
    iteration_count: int
    max_iterations: int
    validation_ready: bool
    current_files: dict  # Current state of edited files
    code_query_decision: str  # Next node to execute
    rag_utils: Optional[RAGUtils]

async def run_forge_subtask(forge_interface, query: str) -> str:
    return await forge_interface.execute_subtask(query)

def code_executor(state: ExecutionState, forge_interface) -> ExecutionState:
    """Execute code changes based on the current implementation_plan."""
    logger.info("Starting code execution")
    append_state_update(state["repo_path"], "execution", "Executing code changes")
    
    # Build query based on iteration count
    if state["iteration_count"] == 1:
        query = f"""
        As an Infrastructure as Code expert, implement the following implementation plan.
        Follow these strict guidelines:
        1. Make changes ONLY to the files mentioned in the implementation plan
        2. Follow IaC best practices for security and configuration 

        Implementation Plan:
        {state["implementation_plan"]}
        """
    else:
        query = f"""
        As an Infrastructure as Code expert, apply the following fixes to match the implementation plan:
        
        Original Implementation Plan (attempted to integrate ):
        {state["implementation_plan"]}
        
        Required Fixes:
        {json.dumps(state["memory"].get("fixes", []), indent=2)}
        """

    try:
        output = asyncio.run(run_forge_subtask(forge_interface, query))
        state["code_execution_output"] = output
        state["code_query_decision"] = "review"
        
        state["messages"] = manage_messages(
            state["messages"], 
            SystemMessage(content=f"Code execution completed: {output[:100]}...")
        )
        
        if 'code_executions' not in state["memory"]:
            state["memory"]['code_executions'] = []
        state["memory"]['code_executions'].append({
            'query': query,
            'output': output,
            'iteration': state["iteration_count"],
            'timestamp': datetime.now().isoformat()
        })
    except Exception as e:
        logger.error(f"Error during code execution: {str(e)}", exc_info=True)
        state["messages"] = manage_messages(
            state["messages"],
            SystemMessage(content=f"Error during code execution: {str(e)}")
        )
        state["code_query_decision"] = "end"

    return state

def review_code(state: ExecutionState) -> ExecutionState:
    """Review the code execution output and decide next steps."""
    logger.info("Starting code review")
    
    # Read current state of files
    files_to_review = state["memory"].get('files_to_edit', [])
    file_contents = {}
    
    for file_path in files_to_review:
        full_path = os.path.join(state["repo_path"], file_path)
        try:
            with open(full_path, 'r') as f:
                file_contents[file_path] = f.read()
        except Exception as e:
            logger.error(f"Error reading file {file_path}: {str(e)}")
            file_contents[file_path] = "Error reading file"
    
    state["current_files"] = file_contents
    
    class ReviewDecision(BaseModel):
        decision: Literal["READY", "FIX", "RAG", "USER"]
        query_or_prompt: str
        reason: str
        issues_found: List[str]
    
    prompt = f"""
    As an Infrastructure as Code expert, review the infrastructure code.

    Current File Contents:
    {json.dumps(file_contents, indent=2)}

    Implementation Plan:
    {state["implementation_plan"]}

    Your job is to determine if the code matches the implementation plan and is ready for validation.

    The goal is to make sure the code is ready to be ran without any errors.

    Review the changes and determine if any of the following are needed:
    1. READY: Code does not have syntax errors, misconfigurations, or placeholder values.
    2. FIX: Code does not match the implementation plan. In this case, provide a query explaining the next changes to be made.
    3. RAG: Need additional context from codebase to fill in missing details, syntax, etc.
    4. USER: Need user input for decisions to fill in missing details (e.g. resource names, an AMI, etc.)
    """

    try:
        # Use structured output instead of parsing JSON
        result = llm.with_structured_output(ReviewDecision).invoke(prompt)
        
        # Store review result
        if 'reviews' not in state["memory"]:
            state["memory"]['reviews'] = []
        state["memory"]['reviews'].append({
            'decision': result.decision,
            'issues': result.issues_found,
            'timestamp': datetime.now().isoformat()
        })
        
        # Set next node based on decision
        state["code_query_decision"] = result.decision.lower()
        
        if result.decision == "FIX":
            if 'fixes' not in state["memory"]:
                state["memory"]['fixes'] = []
            state["memory"]['fixes'].append(result.query_or_prompt)
        elif result.decision == "RAG":
            state["memory"]['current_rag_query'] = result.query_or_prompt
        elif result.decision == "USER":
            state["memory"]['current_user_prompt'] = result.query_or_prompt
            
        state["iteration_count"] += 1
        
        if state["iteration_count"] >= state["max_iterations"]:
            logger.warning("Max iterations reached")
            state["code_query_decision"] = "end"
            
    except Exception as e:
        logger.error(f"Error during code review: {str(e)}", exc_info=True)
        state["code_query_decision"] = "end"
    
    return state

def query_rag(state: ExecutionState) -> ExecutionState:
    """Query RAG for missing info."""
    logger.info("Executing RAG query")
    
    try:
        files_to_query = state["memory"].get('files_to_edit', [])
        rag_query = state["memory"].get('current_rag_query', '')
        
        response = state["rag_utils"].query_codebase(rag_query, files_to_query)
        
        if 'rag_responses' not in state["memory"]:
            state["memory"]['rag_responses'] = []
        state["memory"]['rag_responses'].append({
            'query': rag_query,
            'response': response.answer,
            'relevant_files': response.relevant_files,
            'confidence': response.confidence,
            'timestamp': datetime.now().isoformat()
        })
        
        state["code_query_decision"] = "execute"
        
    except Exception as e:
        logger.error(f"Error during RAG query: {str(e)}", exc_info=True)
        state["code_query_decision"] = "end"
    
    return state

def handle_user_input(state: ExecutionState) -> ExecutionState:
    """Get user input for decisions."""
    logger.info("Getting user input")
    
    try:
        prompt = state["memory"].get('current_user_prompt', '')
        print(f"\n{prompt}")
        
        user_response = input("Your answer: ").strip()
        while True:
            print(f"\nYou entered: {user_response}")
            confirm = input("Confirm this answer? (y/n): ").lower()
            if confirm == 'y':
                break
            elif confirm == 'n':
                print("Please enter your answer again:")
                user_response = input("Your answer: ").strip()
            else:
                print("Please enter 'y' or 'n'")

        if 'user_inputs' not in state["memory"]:
            state["memory"]['user_inputs'] = []
        state["memory"]['user_inputs'].append({
            'prompt': prompt,
            'response': user_response,
            'timestamp': datetime.now().isoformat()
        })
        
        state["code_query_decision"] = "execute"
        
    except Exception as e:
        logger.error(f"Error handling user input: {str(e)}", exc_info=True)
        state["code_query_decision"] = "end"
    
    return state

def create_execution_graph(forge_interface, rag_utils):
    """Creates the execution workflow graph."""
    workflow = StateGraph(ExecutionState)
    
    # Add nodes
    workflow.add_node("execute", lambda s: code_executor(s, forge_interface))
    workflow.add_node("review", review_code)
    workflow.add_node("rag", query_rag)
    workflow.add_node("user", handle_user_input)
    
    # Set entry point
    workflow.set_entry_point("execute")
    
    # Add conditional edges
    workflow.add_conditional_edges(
        "execute",
        lambda x: x["code_query_decision"],
        {
            "review": "review",
            "end": END
        }
    )
    
    workflow.add_conditional_edges(
        "review",
        lambda x: x["code_query_decision"],
        {
            "ready": END,
            "fix": "execute",
            "rag": "rag",
            "user": "user",
            "end": END
        }
    )
    
    # RAG always goes back to execute to incorporate the new information
    workflow.add_edge("rag", "execute")
    
    # User input always goes back to execute to incorporate the new information
    workflow.add_edge("user", "execute")
    
    return workflow.compile()

def start_execution_agent(planning_state: dict, forge_interface, rag_utils: Optional[RAGUtils] = None) -> dict:
    """Start the execution agent with the graph-based workflow."""
    logger.info("Starting execution agent")
    
    # Initialize state with proper message type
    initial_state = ExecutionState(
        messages=[SystemMessage(content="Starting execution process")],
        memory={},
        implementation_plan=planning_state.get('implementation_plan', ''),
        repo_path=planning_state.get('repo_path', ''),
        code_execution_output="",
        iteration_count=1,
        max_iterations=5,
        validation_ready=False,
        current_files={},
        code_query_decision="execute",
        rag_utils=rag_utils
    )
    
    # Create and run the workflow
    workflow = create_execution_graph(forge_interface, rag_utils)
    final_state = workflow.invoke(initial_state)
    
    return final_state
