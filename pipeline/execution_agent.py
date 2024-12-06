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

def code_executor(state: ExecutionState, forge_interface) -> dict:
    """Executes code changes using forge_interface."""
    
    code_changes = state.get('current_query', state.get('implementation_plan', ''))
    
    if not code_changes:
        state['messages'].append(SystemMessage(content="No code changes to execute."))
        state['code_query_decision'] = 'END'
        state['code_query_explanation'] = 'No code changes provided.'
        return state

    logging.info(f"Executing code changes: {code_changes}")
    output = asyncio.run(forge_interface.execute_subtask(code_changes))
    state['code_execution_output'] = output
    
    # Record in memory
    if 'code_executions' not in state['memory']:
        state['memory']['code_executions'] = []
    state['memory']['code_executions'].append({
        'code_changes': code_changes,
        'output': output,
        'timestamp': datetime.now().isoformat()
    })
    
    state['messages'].append(AIMessage(content=f"Executed code changes:\n{code_changes}"))
    return state

def code_review(state: ExecutionState) -> dict:
    """Reviews code changes and detects issues."""
    
    state['iteration_count'] += 1
    if state['iteration_count'] > state.get('max_iterations', 5):
        state['code_query_decision'] = 'END'
        state['code_query_explanation'] = 'Maximum iterations reached.'
        return state

    output = state.get('code_execution_output', '')
    
    prompt = f"""As an Infrastructure as Code expert, review the following code execution output and detect any issues.
    
    Execution Output:
    {output}
    
    Previous Issues (if any):
    {state.get('detected_issues', 'None')}
    
    Analyze for:
    1. Syntax and Format
       - Terraform syntax errors
       - JSON/YAML formatting issues
       - Invalid resource configurations
    
    2. Resource Configuration
       - Invalid resource properties
       - Missing required fields
       - Incompatible resource settings
    
    3. Dependencies
       - Missing provider configurations
       - Unresolved resource dependencies
       - Module version conflicts
    
    4. Security
       - Insecure configurations
       - Missing security groups
       - Overly permissive access
    
    5. Best Practices
       - Resource naming conventions
       - Tag compliance
       - State management concerns
    
    Output your analysis in JSON format with:
    - issues_detected: 'yes' or 'no'
    - issues_list: List of detected issues or 'None'
    """

    class CodeReviewSchema(BaseModel):
        issues_detected: Literal['yes', 'no']
        issues_list: Union[List[str], str]

    try:
        review_output = llm.with_structured_output(CodeReviewSchema).invoke(prompt)
        state['detected_issues'] = review_output.issues_list
        state['messages'].append(SystemMessage(content=f"Code review found issues: {review_output.issues_list}"))
        
        # Record in memory
        if 'code_reviews' not in state['memory']:
            state['memory']['code_reviews'] = []
        state['memory']['code_reviews'].append({
            'issues_detected': review_output.issues_detected,
            'issues_list': review_output.issues_list,
            'timestamp': datetime.now().isoformat()
        })
        
        if review_output.issues_detected == 'yes':
            state['code_query_decision'] = 'Create Query'
        else:
            state['code_query_decision'] = 'END'
            
    except Exception as e:
        logging.error(f"Code review failed: {e}")
        state['messages'].append(SystemMessage(content="Code review failed."))
        state['code_query_decision'] = 'END'
        
    return state

def create_query(state: ExecutionState) -> dict:
    """Creates a new code change query based on detected issues."""
    
    detected_issues = state.get('detected_issues', 'None')
    execution_history = state['memory'].get('code_executions', [])
    review_history = state['memory'].get('code_reviews', [])
    
    prompt = f"""As an Infrastructure as Code expert, create a solution to address the detected issues.
    
    Detected Issues:
    {detected_issues}
    
    Original Implementation Plan:
    {state.get('implementation_plan', 'No plan provided')}
    
    Previous Executions:
    {execution_history[-3:] if execution_history else 'None'}
    
    Previous Reviews:
    {review_history[-3:] if review_history else 'None'}
    
    Create a specific and actionable query that will:
    1. Fix all detected issues
    2. Maintain infrastructure consistency
    3. Follow security best practices
    4. Preserve existing configurations where appropriate
    5. Handle dependencies correctly
    
    Output your response in JSON format with:
    - query: The specific changes to implement, including:
        - Resource modifications
        - Configuration updates
        - Security fixes
        - Dependency adjustments
    """

    class QuerySchema(BaseModel):
        query: str

    try:
        query_output = llm.with_structured_output(QuerySchema).invoke(prompt)
        state['current_query'] = query_output.query
        state['messages'].append(SystemMessage(content=f"Created new query: {query_output.query}"))
        
        # Record in memory
        if 'queries' not in state['memory']:
            state['memory']['queries'] = []
        state['memory']['queries'].append({
            'query': query_output.query,
            'timestamp': datetime.now().isoformat()
        })
        
        state['code_query_decision'] = 'Code Executor'
        
    except Exception as e:
        logging.error(f"Query creation failed: {e}")
        state['messages'].append(SystemMessage(content="Failed to create new query."))
        state['code_query_decision'] = 'END'
        
    return state

def create_execution_graph(forge_interface):
    """Creates the execution workflow graph."""
    
    workflow = StateGraph(ExecutionState)
    
    # Add nodes
    workflow.add_node("code_executor", lambda state: code_executor(state, forge_interface))
    workflow.add_node("code_review", code_review)
    workflow.add_node("create_query", create_query)
    
    # Set entry point
    workflow.set_entry_point("code_executor")
    
    # Add edges
    workflow.add_edge("code_executor", "code_review")
    workflow.add_edge("create_query", "code_executor")
    
    def decision_func(state):
        return state['code_query_decision']
    
    # Add conditional edges
    workflow.add_conditional_edges(
        "code_review",
        decision_func,
        {
            "END": END,
            "Create Query": "create_query"
        }
    )
    
    return workflow.compile()

def start_execution_agent(prior_state: dict, forge_interface):
    """Starts the execution agent with prior state."""
    
    app = create_execution_graph(forge_interface)
    
    # Initialize state
    initial_state = ExecutionState(
        messages=prior_state.get('messages', []),
        current_query=prior_state.get('implementation_plan', ''),
        detected_issues=None,
        iteration_count=0,
        max_iterations=5,
        memory={},
        implementation_plan=prior_state.get('implementation_plan', ''),
        code_execution_output=None,
        code_query_decision=None,
        code_query_explanation=None
    )
    
    # Initialize memory components
    memory_keys = ['code_executions', 'code_reviews', 'queries']
    for key in memory_keys:
        initial_state['memory'][key] = []
    
    # Start the workflow
    final_state = app.invoke(initial_state)
    return final_state