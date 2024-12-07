from langgraph.graph import StateGraph, END
from typing import TypedDict, List, Union, Optional, Annotated, Literal
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel
from datetime import datetime
from langgraph.store.memory import InMemoryStore
import logging
import os
import asyncio
import subprocess
from pathlib import Path

# Initialize components
store = InMemoryStore()
logging.basicConfig(level=logging.INFO)
llm = ChatOpenAI(model="gpt-4o", temperature=0)

class ValidationState(TypedDict):
    messages: List[BaseMessage]
    implementation_plan: str
    current_command: Optional[str]
    command_output: Optional[str]
    validation_status: Optional[str]
    detected_errors: Optional[Union[List[str], str]]
    iteration_count: int
    max_iterations: int
    memory: dict
    file_context: Optional[dict]
    linting_result: Optional[dict]

def command_generator(state: ValidationState) -> dict:
    """Generates validation commands from implementation plan."""
    
    prompt = f"""
    Based on the implementation plan, generate the next validation command to run.
    Consider previous commands and their outcomes when suggesting the next command.
    
    Implementation Plan:
    {state['implementation_plan']}
    
    Previous Commands and Outputs:
    {state['memory'].get('command_history', [])}
    
    Output in JSON format:
    - command: The command to run
    - expected_output: What the command should output if successful
    - validation_criteria: List of criteria to check in the output
    """
    
    class CommandSchema(BaseModel):
        command: str
        expected_output: str
        validation_criteria: List[str]
    
    try:
        command_output = llm.with_structured_output(CommandSchema).invoke(prompt)
        state['current_command'] = command_output.command
        
        # Store in memory
        if 'command_history' not in state['memory']:
            state['memory']['command_history'] = []
        state['memory']['command_history'].append({
            'command': command_output.command,
            'expected_output': command_output.expected_output,
            'criteria': command_output.validation_criteria,
            'timestamp': datetime.now().isoformat()
        })
        
        state['messages'].append(SystemMessage(content=f"Generated command: {command_output.command}"))
        state['validation_status'] = 'Execute'
        
    except Exception as e:
        logging.error(f"Command generation failed: {e}")
        state['validation_status'] = 'END'
        
    return state

def command_executor(state: ValidationState) -> dict:
    """Executes the validation command."""
    
    command = state.get('current_command')
    if not command:
        state['validation_status'] = 'END'
        return state
    
    try:
        # Execute command using subprocess
        result = subprocess.run(command, shell=True, capture_output=True, text=True)
        state['command_output'] = result.stdout if result.returncode == 0 else result.stderr
        
        # Store result in memory
        if 'execution_results' not in state['memory']:
            state['memory']['execution_results'] = []
        state['memory']['execution_results'].append({
            'command': command,
            'output': state['command_output'],
            'success': result.returncode == 0,
            'timestamp': datetime.now().isoformat()
        })
        
        state['messages'].append(SystemMessage(content=f"Executed command: {command}\nOutput: {state['command_output']}"))
        state['validation_status'] = 'Validate'
        
    except Exception as e:
        logging.error(f"Command execution failed: {e}")
        state['command_output'] = str(e)
        state['validation_status'] = 'Error Handler'
        
    return state

def output_validator(state: ValidationState) -> dict:
    """Validates command output against expected results."""
    
    command_history = state['memory'].get('command_history', [])
    current_command = next((cmd for cmd in command_history if cmd['command'] == state['current_command']), None)
    
    if not current_command:
        state['validation_status'] = 'END'
        return state
    
    prompt = f"""As an Infrastructure as Code expert, validate the output of this infrastructure command.
    
    Command: {state['current_command']}
    Actual Output: {state['command_output']}
    
    Expected Output: {current_command['expected_output']}
    Validation Criteria: {current_command['validation_criteria']}
    
    Validate the following aspects:
    1. Resource Creation/Modification
       - Resources created/updated successfully
       - Correct resource configurations
       - Proper resource relationships
    
    2. State Management
       - Terraform state consistency
       - No state conflicts
       - Proper state tracking
    
    3. Configuration Validation
       - All required settings applied
       - Correct value types
       - Valid configuration combinations
    
    4. Security Compliance
       - Security groups properly configured
       - IAM roles and policies correct
       - Encryption settings applied
    
    5. Network Configuration
       - VPC settings correct
       - Subnet configurations valid
       - Route tables properly updated
    
    6. Service Integration
       - Service endpoints accessible
       - Cross-service permissions correct
       - API configurations valid
    
    Output in JSON format:
    - is_valid: true/false
    - issues: List of issues found (empty if valid)
    - suggestions: List of suggestions to fix issues
    """
    
    class ValidationSchema(BaseModel):
        is_valid: bool
        issues: List[str]
        suggestions: List[str]
    
    try:
        validation = llm.with_structured_output(ValidationSchema).invoke(prompt)
        
        # Store validation result
        if 'validations' not in state['memory']:
            state['memory']['validations'] = []
        state['memory']['validations'].append({
            'command': state['current_command'],
            'is_valid': validation.is_valid,
            'issues': validation.issues,
            'timestamp': datetime.now().isoformat()
        })
        
        if validation.is_valid:
            state['validation_status'] = 'Lint'
        else:
            state['detected_errors'] = validation.issues
            state['validation_status'] = 'Error Handler'
            
        state['messages'].append(SystemMessage(content=f"Validation {'passed' if validation.is_valid else 'failed'}: {validation.issues}"))
        
    except Exception as e:
        logging.error(f"Validation failed: {e}")
        state['validation_status'] = 'Error Handler'
        
    return state

def error_handler(state: ValidationState) -> dict:
    """Handles errors by generating queries for forge."""
    
    # Get relevant file context
    file_context = state.get('file_context', {})
    
    prompt = f"""
    Generate a query to fix the detected errors.
    
    Command: {state['current_command']}
    Output: {state['command_output']}
    Detected Errors: {state['detected_errors']}
    
    File Context:
    {file_context}
    
    Previous Attempts:
    {state['memory'].get('error_fixes', [])}
    
    Generate a specific query for forge to fix these issues.
    Output in JSON format:
    - query: The query for forge
    - files_to_modify: List of files that need modification
    """
    
    class ErrorFixSchema(BaseModel):
        query: str
        files_to_modify: List[str]
    
    try:
        fix = llm.with_structured_output(ErrorFixSchema).invoke(prompt)
        
        # Store error fix attempt
        if 'error_fixes' not in state['memory']:
            state['memory']['error_fixes'] = []
        state['memory']['error_fixes'].append({
            'errors': state['detected_errors'],
            'query': fix.query,
            'files': fix.files_to_modify,
            'timestamp': datetime.now().isoformat()
        })
        
        # Send query to forge
        # This would integrate with your forge interface
        # For now, we'll just store it
        state['messages'].append(SystemMessage(content=f"Generated fix query: {fix.query}"))
        state['validation_status'] = 'Command Generator'
        
    except Exception as e:
        logging.error(f"Error handling failed: {e}")
        state['validation_status'] = 'END'
        
    return state

def output_linter(state: ValidationState) -> dict:
    """Lints the command output for quality and best practices."""
    
    prompt = f"""As an Infrastructure as Code expert, lint this infrastructure command output for quality and best practices.
    
    Command: {state['current_command']}
    Output: {state['command_output']}
    
    Analyze the following aspects:
    1. Resource Optimization
       - Instance sizing appropriate
       - Resource utilization efficient
       - Cost optimization opportunities
    
    2. Security Best Practices
       - Principle of least privilege
       - Network security groups
       - Data encryption
       - Access controls
    
    3. Operational Excellence
       - Monitoring and logging
       - Backup and recovery
       - Scalability considerations
       - Maintenance windows
    
    4. Reliability
       - High availability setup
       - Fault tolerance
       - Disaster recovery
       - Service limits
    
    5. Performance Efficiency
       - Resource placement
       - Network latency
       - Service integration efficiency
       - Caching strategies
    
    6. Cost Optimization
       - Resource rightsizing
       - Reserved capacity usage
       - Lifecycle management
       - Cost allocation tags
    
    Output in JSON format:
    - passes_lint: true/false
    - lint_issues: List of linting issues found
    - severity: high/medium/low for each issue
    """
    
    class LintSchema(BaseModel):
        passes_lint: bool
        lint_issues: List[str]
        severity: List[str]
    
    try:
        lint_result = llm.with_structured_output(LintSchema).invoke(prompt)
        
        # Store lint result
        if 'lint_results' not in state['memory']:
            state['memory']['lint_results'] = []
        state['memory']['lint_results'].append({
            'command': state['current_command'],
            'passes_lint': lint_result.passes_lint,
            'issues': lint_result.lint_issues,
            'severity': lint_result.severity,
            'timestamp': datetime.now().isoformat()
        })
        
        if lint_result.passes_lint:
            state['validation_status'] = 'Command Generator'
        else:
            state['detected_errors'] = lint_result.lint_issues
            state['validation_status'] = 'Error Handler'
            
        state['messages'].append(SystemMessage(content=f"Lint {'passed' if lint_result.passes_lint else 'failed'}: {lint_result.lint_issues}"))
        
    except Exception as e:
        logging.error(f"Linting failed: {e}")
        state['validation_status'] = 'Error Handler'
        
    return state

def create_validation_graph():
    """Creates the validation workflow graph."""
    
    workflow = StateGraph(ValidationState)
    
    # Add nodes
    workflow.add_node("command_generator", command_generator)
    workflow.add_node("command_executor", command_executor)
    workflow.add_node("output_validator", output_validator)
    workflow.add_node("error_handler", error_handler)
    workflow.add_node("output_linter", output_linter)
    
    # Set entry point
    workflow.set_entry_point("command_generator")
    
    # Add edges
    workflow.add_edge("command_generator", "command_executor")
    workflow.add_edge("command_executor", "output_validator")
    workflow.add_edge("output_validator", "output_linter")
    
    def decision_func(state):
        return state['validation_status']
    
    # Add conditional edges
    workflow.add_conditional_edges(
        "output_validator",
        decision_func,
        {
            "END": END,
            "Lint": "output_linter",
            "Error Handler": "error_handler"
        }
    )
    
    workflow.add_conditional_edges(
        "output_linter",
        decision_func,
        {
            "END": END,
            "Command Generator": "command_generator",
            "Error Handler": "error_handler"
        }
    )
    
    workflow.add_conditional_edges(
        "error_handler",
        decision_func,
        {
            "END": END,
            "Command Generator": "command_generator"
        }
    )
    
    return workflow.compile()

def start_validation_agent(implementation_plan: str, forge_interface) -> dict:
    """Starts the validation agent."""
    
    app = create_validation_graph()
    
    # Initialize state
    initial_state = ValidationState(
        messages=[],
        implementation_plan=implementation_plan,
        current_command=None,
        command_output=None,
        validation_status=None,
        detected_errors=None,
        iteration_count=0,
        max_iterations=10,
        memory={},
        file_context={},
        linting_result=None
    )
    
    # Initialize memory components
    memory_keys = ['command_history', 'execution_results', 'validations', 'error_fixes', 'lint_results']
    for key in memory_keys:
        initial_state['memory'][key] = []
    
    # Start the workflow
    final_state = app.invoke(initial_state)
    return final_state 