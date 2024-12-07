from langgraph.graph import StateGraph, END
from typing import TypedDict, List, Union, Optional, Annotated, Literal
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel
from datetime import datetime
import logging
import os
import asyncio
from pathlib import Path
from utils.rag_utils import RAGUtils
from utils.state_utils import append_state_update
import json

# Initialize components
logging.basicConfig(level=logging.INFO)
llm = ChatOpenAI(model="gpt-4", temperature=0)

class ExecutionState(TypedDict):
    messages: List[BaseMessage]
    current_query: Optional[str]
    detected_issues: Optional[Union[List[str], str]]
    iteration_count: int
    max_iterations: int
    memory: dict
    implementation_plan: Optional[str]
    code_execution_output: Optional[str]
    code_query_decision: Optional[str]
    code_query_explanation: Optional[str]
    relevant_files: List[str]
    rag_query: Optional[str]
    rag_response: Optional[str]
    info_needed: Optional[str]
    linting_result: Optional[dict]
    user_input_needed: Optional[dict]
    validation_ready: bool
    repo_path: str

class LintingResult(BaseModel):
    has_issues: bool
    issues: List[str]
    needs_user_input: bool
    user_prompts: List[str]
    rag_queries: List[str]

def code_executor(state: ExecutionState, forge_interface) -> dict:
    """Executes code changes using forge_interface."""
    append_state_update(state["repo_path"], "execution", "Executing code changes")
    
    try:
        # Build comprehensive context for the forge
        context = {
            'implementation_plan': state.get('implementation_plan', ''),
            'user_inputs': state['memory'].get('user_inputs', []),
            'rag_responses': state['memory'].get('rag_responses', []),
            'previous_executions': state['memory'].get('code_executions', []),
            'detected_issues': state.get('detected_issues', []),
            'linting_result': state.get('linting_result', {})
        }

        # Create intelligent query for forge
        query = f"""
        Based on the following context, implement the necessary code changes:

        Implementation Plan:
        {context['implementation_plan']}

        User Inputs:
        {json.dumps(context['user_inputs'], indent=2)}

        Previous Executions:
        {json.dumps(context['previous_executions'], indent=2)}

        Detected Issues:
        {json.dumps(context['detected_issues'], indent=2)}

        Linting Results:
        {json.dumps(context['linting_result'], indent=2)}

        Please implement these changes, ensuring all user requirements and context are properly incorporated.
        """

        # Execute the implementation through forge
        output = asyncio.run(forge_interface.execute_subtask(query))
        state['code_execution_output'] = output
        
        # Record execution in memory
        if 'code_executions' not in state['memory']:
            state['memory']['code_executions'] = []
        state['memory']['code_executions'].append({
            'query': query,
            'output': output,
            'timestamp': datetime.now().isoformat()
        })
        
        state['code_query_decision'] = 'Code Review'
        
    except Exception as e:
        state['messages'].append(SystemMessage(content=f"Error during code execution: {str(e)}"))
        state['code_query_decision'] = 'END'
    
    return state

def rag_query(state: ExecutionState, repo_path: str) -> dict:
    """Executes RAG query to get additional context."""
    append_state_update(repo_path, "execution", "Executing RAG query")
    
    try:
        query = state.get('rag_query')
        if not query:
            state['code_query_decision'] = 'Create Query'
            return state
            
        # Initialize RAG utils
        rag_utils = RAGUtils(repo_path)
        
        # Execute RAG query
        response = rag_utils.query(query)
        
        # Store response in memory
        if 'rag_responses' not in state['memory']:
            state['memory']['rag_responses'] = []
        state['memory']['rag_responses'].append({
            'query': query,
            'response': response,
            'timestamp': datetime.now().isoformat()
        })
        
        state['rag_response'] = response
        state['code_query_decision'] = 'Create Query'
        
    except Exception as e:
        logging.error(f"RAG query failed: {e}")
        state['messages'].append(SystemMessage(content=f"Error during RAG query: {str(e)}"))
        state['code_query_decision'] = 'END'
    
    return state

def create_execution_graph(forge_interface, repo_path: str):
    """Creates the execution workflow graph."""
    
    workflow = StateGraph(ExecutionState)
    
    # Add nodes
    workflow.add_node("code_executor", lambda state: code_executor(state, forge_interface))
    workflow.add_node("code_review", lambda state: code_review(state, repo_path))
    workflow.add_node("rag_query_node", lambda state: rag_query(state, repo_path))
    workflow.add_node("create_query", create_query)
    workflow.add_node("handle_user_input", handle_user_input)
    
    # Set entry point to create_query instead of code_executor
    workflow.set_entry_point("create_query")
    
    # Add edges
    workflow.add_edge("code_executor", "code_review")
    workflow.add_edge("create_query", "code_executor")
    workflow.add_edge("rag_query_node", "create_query")
    workflow.add_edge("handle_user_input", "create_query")
    
    def decision_func(state):
        if state.get('validation_ready'):
            return 'END'
        decision = state.get('code_query_decision')
        # Map decisions to valid edges
        decision_map = {
            'Code Executor': 'Create Query',
            'Code Review': 'Code Review',
            'RAG Query': 'RAG Query',
            'User Input': 'User Input',
            'Create Query': 'Create Query',
            'END': 'END'
        }
        return decision_map.get(decision, 'END')
    
    # Add conditional edges with corrected mapping
    workflow.add_conditional_edges(
        "code_review",
        decision_func,
        {
            "END": END,
            "Create Query": "create_query",
            "RAG Query": "rag_query_node",
            "User Input": "handle_user_input"
        }
    )
    
    return workflow.compile()

def code_review(state: ExecutionState, repo_path: str) -> dict:
    """Reviews code changes and detects issues."""
    append_state_update(repo_path, "execution", "Reviewing code changes")
    
    state['iteration_count'] += 1
    if state['iteration_count'] > state.get('max_iterations', 5):
        state['code_query_decision'] = 'END'
        return state

    try:
        # Get forge response and file contents
        forge_output = state.get('code_execution_output', '')
        
        # Get memory of previous reviews
        previous_reviews = state['memory'].get('code_reviews', [])
        
        prompt = f"""Review the code changes and forge output for issues that require:
        1. User input for missing information (e.g., paths, names, IDs)
        2. RAG queries for context or existing configurations
        3. No issues - ready for validation
        
        Forge Output:
        {forge_output}
        
        Previous Reviews:
        {previous_reviews}
        
        Previous Issues:
        {state.get('detected_issues', 'None')}
        
        Respond with a JSON object containing:
        - next_step: "user_input", "rag_query", or "validation"
        - reason: String explaining why this step is needed
        - query: String containing the RAG query if needed
        - user_prompt: String containing the specific information needed from user if needed
        """
        
        class ReviewResult(BaseModel):
            next_step: Literal["user_input", "rag_query", "validation"]
            reason: str
            query: Optional[str]
            user_prompt: Optional[str]
        
        result = llm.with_structured_output(ReviewResult).invoke(prompt)
        
        # Store review result in memory
        if 'code_reviews' not in state['memory']:
            state['memory']['code_reviews'] = []
        state['memory']['code_reviews'].append({
            'result': result.dict(),
            'timestamp': datetime.now().isoformat()
        })
        
        # Set next action based on review
        if result.next_step == "validation":
            state['validation_ready'] = True
            state['code_query_decision'] = 'END'
            logging.info(f"Ready for validation: {result.reason}")
        elif result.next_step == "user_input":
            state['code_query_decision'] = 'User Input'
            state['user_input_needed'] = {'prompt': result.user_prompt}
            logging.info(f"User input needed: {result.user_prompt}")
        else:  # rag_query
            state['code_query_decision'] = 'RAG Query'
            state['rag_query'] = result.query
            logging.info(f"RAG query needed: {result.query}")
            
    except Exception as e:
        logging.error(f"Code review failed: {e}")
        state['messages'].append(SystemMessage(content=f"Error during code review: {str(e)}"))
        state['code_query_decision'] = 'END'
    
    return state

def handle_user_input(state: ExecutionState) -> dict:
    """Handles gathering user input for specific information needs."""
    append_state_update(state["repo_path"], "execution", "Getting user input")
    
    if not state.get('user_input_needed', {}).get('prompt'):
        state['code_query_decision'] = 'Code Review'
        return state
    
    try:
        prompt = state['user_input_needed']['prompt']
        logging.info(f"User Input Required: {prompt}")
        
        # Get user input via CLI
        print("\n" + prompt)
        user_response = input("Your answer: ").strip()
        
        # Confirm the input
        while True:
            print(f"\nYou entered: {user_response}")
            confirm = input("Confirm this answer? (y/n): ").lower()
            if confirm == 'y':
                break
            elif confirm == 'n':
                print("\nPlease enter your answer again:")
                user_response = input("Your answer: ").strip()
            else:
                print("\nPlease enter 'y' or 'n'")
        
        # Store the prompt and response for use in next query
        if 'user_inputs' not in state['memory']:
            state['memory']['user_inputs'] = []
        state['memory']['user_inputs'].append({
            'prompt': prompt,
            'response': user_response,
            'timestamp': datetime.now().isoformat()
        })
        
        # Move to create query to handle the user input
        state['code_query_decision'] = 'Create Query'
        
    except Exception as e:
        logging.error(f"User input handling failed: {str(e)}")
        state['messages'].append(SystemMessage(content=f"Error handling user input: {str(e)}"))
        state['code_query_decision'] = 'END'
    
    return state

def create_query(state: ExecutionState) -> dict:
    """Creates a new code change query based on gathered information."""
    append_state_update(state["repo_path"], "execution", "Creating code query")
    
    try:
        # Build context for query creation
        context = {
            'user_inputs': state['memory'].get('user_inputs', []),
            'rag_responses': state['memory'].get('rag_responses', []),
            'previous_executions': state['memory'].get('code_executions', []),
            'implementation_plan': state.get('implementation_plan', '')
        }
        
        # Create query for the LLM
        prompt = f"""Based on the following context, determine if we have enough information to proceed with implementation:

        Implementation Plan:
        {context['implementation_plan']}

        User Inputs:
        {json.dumps(context['user_inputs'], indent=2)}

        Previous Executions:
        {json.dumps(context['previous_executions'], indent=2)}

        RAG Responses:
        {json.dumps(context['rag_responses'], indent=2)}

        If any critical information is missing, specify exactly what's needed.
        If we have all needed information, respond with 'READY'.
        """
        
        response = llm.invoke(prompt)
        
        if 'READY' in response.content:
            state['code_query_decision'] = 'Code Executor'
        else:
            state['user_input_needed'] = {
                'prompt': response.content
            }
            state['code_query_decision'] = 'User Input'
        
    except Exception as e:
        logging.error(f"Query creation failed: {str(e)}")
        state['messages'].append(SystemMessage(content=f"Error during query creation: {str(e)}"))
        state['code_query_decision'] = 'END'
    
    return state

def create_execution_graph(forge_interface, repo_path: str):
    """Creates the execution workflow graph."""
    
    workflow = StateGraph(ExecutionState)
    
    # Add nodes
    workflow.add_node("code_executor", lambda state: code_executor(state, forge_interface))
    workflow.add_node("code_review", lambda state: code_review(state, repo_path))
    workflow.add_node("rag_query_node", lambda state: rag_query(state, repo_path))
    workflow.add_node("create_query", create_query)
    workflow.add_node("handle_user_input", handle_user_input)
    
    # Set entry point
    workflow.set_entry_point("code_executor")
    
    # Add edges
    workflow.add_edge("code_executor", "code_review")
    workflow.add_edge("create_query", "code_executor")
    workflow.add_edge("rag_query_node", "create_query")
    workflow.add_edge("handle_user_input", "create_query")
    
    def decision_func(state):
        if state.get('validation_ready'):
            return 'END'
        return state['code_query_decision']
    
    # Add conditional edges
    workflow.add_conditional_edges(
        "code_review",
        decision_func,
        {
            "END": END,
            "Create Query": "create_query",
            "RAG Query": "rag_query_node",
            "User Input": "handle_user_input"
        }
    )
    
    return workflow.compile()

def start_execution_agent(planning_state: dict, forge_interface) -> dict:
    """Starts the execution agent with prior state."""
    
    try:
        # Initialize state with required fields from planning state
        initial_state = ExecutionState(
            messages=[],
            current_query=None,
            detected_issues=None,
            iteration_count=0,
            max_iterations=5,
            memory={},
            implementation_plan=planning_state.get('implementation_plan', ''),
            code_execution_output=None,
            code_query_decision=None,
            code_query_explanation=None,
            relevant_files=planning_state.get('files_to_edit', []),
            rag_query=None,
            rag_response=None,
            info_needed=None,
            linting_result=None,
            user_input_needed=None,
            validation_ready=False,
            repo_path=planning_state.get('repo_path', '')
        )
        
        # Execute the code changes
        workflow = create_execution_graph(forge_interface, planning_state['repo_path'])
        final_state = workflow.invoke(initial_state)
        
        return final_state
        
    except Exception as e:
        raise