from langgraph.graph import StateGraph, END
from typing import TypedDict, List, Union, Optional, Annotated, Literal
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel
from datetime import datetime
import logging
import os
import asyncio
import subprocess
from pathlib import Path

# Initialize components
logging.basicConfig(level=logging.INFO)
llm = ChatOpenAI(model="gpt-4", temperature=0)

class FinalizationState(TypedDict):
    messages: List[BaseMessage]
    implementation_plan: str
    current_step: Optional[str]
    step_output: Optional[str]
    finalization_status: Optional[str]
    detected_errors: Optional[Union[List[str], str]]
    iteration_count: int
    max_iterations: int
    memory: dict
    git_context: Optional[dict]

def step_extractor(state: FinalizationState) -> dict:
    """Extracts the next finalization step from the implementation plan."""
    
    prompt = f"""As an Infrastructure as Code expert, identify the next infrastructure finalization step.
    
    Implementation Plan:
    {state['implementation_plan']}
    
    Previous Steps:
    {state['memory'].get('step_history', [])}
    
    Focus on identifying the next appropriate step in this STRICT order:
    1. Infrastructure Application
       - terraform init (if not already done)
       - terraform plan (to verify changes)
       - terraform apply -auto-approve (to apply changes)
       - terraform output (to capture outputs)
       - aws configure (if AWS credentials needed)
       - aws commands to verify resources
    
    2. Resource Verification
       - Check resource existence
       - Verify resource configurations
       - Test resource accessibility
       - Validate resource states
       - Confirm resource relationships
    
    3. Integration Testing
       - Test service connections
       - Verify API endpoints
       - Check network connectivity
       - Validate load balancers
       - Test auto-scaling
    
    4. Security Verification
       - Verify security groups
       - Check IAM roles/policies
       - Validate encryption
       - Test access controls
       - Verify network ACLs
    
    5. Documentation & Version Control
       - Update README
       - Document changes
       - Git commit
       - Git tag
       - Git push
    
    IMPORTANT: Infrastructure changes MUST be applied before moving to verification steps.
    Do not proceed to verification until terraform apply is successful.
    
    Output in JSON format:
    - step_type: One of ['terraform', 'git', 'config', 'verify', 'docs', 'END']
    - command: The exact command to run
    - description: Detailed description of what this step does and why
    - expected_output: What to expect from successful execution
    - requires_apply: true if this step applies infrastructure changes
    """
    
    class StepSchema(BaseModel):
        step_type: Literal['terraform', 'git', 'config', 'verify', 'docs', 'END']
        command: str
        description: str
        expected_output: str
        requires_apply: bool
    
    try:
        step_output = llm.with_structured_output(StepSchema).invoke(prompt)
        state['current_step'] = step_output.command
        
        # Store in memory
        if 'step_history' not in state['memory']:
            state['memory']['step_history'] = []
        state['memory']['step_history'].append({
            'type': step_output.step_type,
            'command': step_output.command,
            'description': step_output.description,
            'expected_output': step_output.expected_output,
            'requires_apply': step_output.requires_apply,
            'timestamp': datetime.now().isoformat()
        })
        
        # Check if infrastructure has been applied before allowing verification steps
        if not state['memory'].get('infrastructure_applied', False):
            if step_output.step_type in ['verify', 'docs', 'git'] and not step_output.requires_apply:
                logging.warning("Cannot proceed to verification before applying infrastructure changes")
                state['current_step'] = 'terraform apply -auto-approve'
                state['finalization_status'] = 'Execute'
                return state
        
        if step_output.step_type == 'END':
            # Only allow END if infrastructure has been applied
            if state['memory'].get('infrastructure_applied', False):
                state['finalization_status'] = 'END'
            else:
                logging.warning("Cannot end before applying infrastructure changes")
                state['current_step'] = 'terraform apply -auto-approve'
                state['finalization_status'] = 'Execute'
        else:
            state['finalization_status'] = 'Execute'
            
        state['messages'].append(SystemMessage(content=f"Next step: {step_output.description}"))
        
    except Exception as e:
        logging.error(f"Step extraction failed: {e}")
        state['finalization_status'] = 'END'
        
    return state

def step_executor(state: FinalizationState) -> dict:
    """Executes the current finalization step."""
    
    command = state.get('current_step')
    if not command:
        state['finalization_status'] = 'END'
        return state
    
    try:
        # Execute command using subprocess
        result = subprocess.run(command, shell=True, capture_output=True, text=True)
        state['step_output'] = result.stdout if result.returncode == 0 else result.stderr
        
        # Check if this was an infrastructure apply step
        current_step = state['memory']['step_history'][-1]
        if current_step.get('requires_apply', False):
            if result.returncode == 0:
                state['memory']['infrastructure_applied'] = True
                logging.info("Infrastructure changes successfully applied")
            else:
                logging.error("Failed to apply infrastructure changes")
                state['memory']['infrastructure_applied'] = False
        
        # Store result in memory
        if 'execution_results' not in state['memory']:
            state['memory']['execution_results'] = []
        state['memory']['execution_results'].append({
            'command': command,
            'output': state['step_output'],
            'success': result.returncode == 0,
            'requires_apply': current_step.get('requires_apply', False),
            'timestamp': datetime.now().isoformat()
        })
        
        state['messages'].append(SystemMessage(content=f"Executed step: {command}\nOutput: {state['step_output']}"))
        state['finalization_status'] = 'Verify'
        
    except Exception as e:
        logging.error(f"Step execution failed: {e}")
        state['step_output'] = str(e)
        state['finalization_status'] = 'Error Handler'
        
    return state

def step_verifier(state: FinalizationState) -> dict:
    """Verifies the output of the executed step."""
    
    step_history = state['memory'].get('step_history', [])
    current_step = next((step for step in step_history if step['command'] == state['current_step']), None)
    
    if not current_step:
        state['finalization_status'] = 'END'
        return state
    
    prompt = f"""As an Infrastructure as Code expert, verify the output of this infrastructure finalization step.
    
    Step Type: {current_step['type']}
    Command: {state['current_step']}
    Actual Output: {state['step_output']}
    Expected Output: {current_step['expected_output']}
    
    Verify based on step type:
    
    For Terraform steps:
    - Plan shows expected changes
    - Apply completed successfully
    - Resources created/updated correctly
    - No state conflicts
    - Outputs match expectations
    
    For Verification steps:
    - Resources are accessible
    - Configurations are correct
    - Services are responding
    - Permissions are working
    - Metrics are available
    
    For Configuration steps:
    - Settings applied correctly
    - Environment variables set
    - Secrets accessible
    - Services configured properly
    - Dependencies resolved
    
    For Git steps:
    - Changes committed
    - History clean
    - Tags applied
    - Remote updated
    - No conflicts
    
    For Documentation steps:
    - Files updated
    - Content accurate
    - Examples included
    - Format consistent
    - Links working
    
    Output in JSON format:
    - is_successful: true/false
    - issues: List of issues found (empty if successful)
    - suggestions: List of suggestions to fix issues
    """
    
    class VerificationSchema(BaseModel):
        is_successful: bool
        issues: List[str]
        suggestions: List[str]
    
    try:
        verification = llm.with_structured_output(VerificationSchema).invoke(prompt)
        
        # Store verification result
        if 'verifications' not in state['memory']:
            state['memory']['verifications'] = []
        state['memory']['verifications'].append({
            'step': state['current_step'],
            'is_successful': verification.is_successful,
            'issues': verification.issues,
            'timestamp': datetime.now().isoformat()
        })
        
        if verification.is_successful:
            state['finalization_status'] = 'Step Extractor'
        else:
            state['detected_errors'] = verification.issues
            state['finalization_status'] = 'Error Handler'
            
        state['messages'].append(SystemMessage(content=f"Verification {'passed' if verification.is_successful else 'failed'}: {verification.issues}"))
        
    except Exception as e:
        logging.error(f"Verification failed: {e}")
        state['finalization_status'] = 'Error Handler'
        
    return state

def error_handler(state: FinalizationState) -> dict:
    """Handles errors in finalization steps."""
    
    prompt = f"""As an Infrastructure as Code expert, analyze and fix this infrastructure error.
    
    Step Type: {state['memory']['step_history'][-1]['type']}
    Command: {state['current_step']}
    Output: {state['step_output']}
    Detected Errors: {state['detected_errors']}
    
    Previous Attempts:
    {state['memory'].get('error_fixes', [])}
    
    Infrastructure Applied: {state['memory'].get('infrastructure_applied', False)}
    
    Generate a solution to fix these issues.
    If this is an infrastructure apply error, prioritize fixing the terraform configuration.
    
    Output in JSON format:
    - fix_type: One of ['retry', 'alternative', 'rollback', 'manual']
    - fix_command: The command to run (if applicable)
    - description: Description of the fix
    - requires_apply: true if this fix applies infrastructure changes
    """
    
    class ErrorFixSchema(BaseModel):
        fix_type: Literal['retry', 'alternative', 'rollback', 'manual']
        fix_command: Optional[str]
        description: str
        requires_apply: bool
    
    try:
        fix = llm.with_structured_output(ErrorFixSchema).invoke(prompt)
        
        # Store error fix attempt
        if 'error_fixes' not in state['memory']:
            state['memory']['error_fixes'] = []
        state['memory']['error_fixes'].append({
            'errors': state['detected_errors'],
            'fix_type': fix.fix_type,
            'fix_command': fix.fix_command,
            'description': fix.description,
            'requires_apply': fix.requires_apply,
            'timestamp': datetime.now().isoformat()
        })
        
        if fix.fix_type == 'manual':
            state['finalization_status'] = 'END'
            state['messages'].append(SystemMessage(content=f"Manual intervention required: {fix.description}"))
        else:
            state['current_step'] = fix.fix_command
            state['finalization_status'] = 'Execute'
            state['messages'].append(SystemMessage(content=f"Attempting fix: {fix.description}"))
        
    except Exception as e:
        logging.error(f"Error handling failed: {e}")
        state['finalization_status'] = 'END'
        
    return state

def create_finalization_graph():
    """Creates the finalization workflow graph."""
    
    workflow = StateGraph(FinalizationState)
    
    # Add nodes
    workflow.add_node("step_extractor", step_extractor)
    workflow.add_node("step_executor", step_executor)
    workflow.add_node("step_verifier", step_verifier)
    workflow.add_node("error_handler", error_handler)
    
    # Set entry point
    workflow.set_entry_point("step_extractor")
    
    # Add edges
    workflow.add_edge("step_extractor", "step_executor")
    workflow.add_edge("step_executor", "step_verifier")
    
    def decision_func(state):
        return state['finalization_status']
    
    # Add conditional edges
    workflow.add_conditional_edges(
        "step_verifier",
        decision_func,
        {
            "END": END,
            "Step Extractor": "step_extractor",
            "Error Handler": "error_handler"
        }
    )
    
    workflow.add_conditional_edges(
        "error_handler",
        decision_func,
        {
            "END": END,
            "Execute": "step_executor"
        }
    )
    
    workflow.add_conditional_edges(
        "step_extractor",
        decision_func,
        {
            "END": END,
            "Execute": "step_executor"
        }
    )
    
    return workflow.compile()

def start_finalization_agent(implementation_plan: str) -> dict:
    """Starts the finalization agent."""
    
    app = create_finalization_graph()
    
    # Initialize state
    initial_state = FinalizationState(
        messages=[],
        implementation_plan=implementation_plan,
        current_step=None,
        step_output=None,
        finalization_status=None,
        detected_errors=None,
        iteration_count=0,
        max_iterations=10,
        memory={},
        git_context={}
    )
    
    # Initialize memory components
    memory_keys = ['step_history', 'execution_results', 'verifications', 'error_fixes']
    for key in memory_keys:
        initial_state['memory'][key] = []
    
    # Start the workflow
    final_state = app.invoke(initial_state)
    return final_state 