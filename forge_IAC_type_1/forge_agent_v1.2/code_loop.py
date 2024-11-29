# secondary_agent.py

from langgraph.graph import StateGraph, END
from utils.git_utils import clone_repository, create_combined_txt
from utils.workflow_utils import execute_parallel_tasks, configure_aws_from_env
from typing import TypedDict, List, Union, Optional, Annotated
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, SystemMessage
import os
import logging
from langgraph.graph import add_messages
from typing import Literal
from utils.workflow_utils import setup_AWS_state, generate_file_descriptions, generate_codebase_overview
from rag_agent import retrieve_information
from langchain_openai import ChatOpenAI
from pydantic import BaseModel
from datetime import datetime
from langgraph.store.memory import InMemoryStore  # Use a persistent store in production
from setup_agent import AgentState  # Import AgentState from your setup agent

# Initialize the memory store
store = InMemoryStore()

# Setup logging
logging.basicConfig(level=logging.INFO)
llm = ChatOpenAI(model="gpt-4o", temperature=0)

# Ensure that the forge_interface is properly imported or defined
from utils.subprocess_handler import SubprocessHandler
from utils.forge_interface import ForgeInterface

# Replace with your actual repository path
REPO_PATH = os.getenv("REPO_PATH", "/path/to/repo")

# Initialize SubprocessHandler and ForgeInterface
subprocess_handler = SubprocessHandler(REPO_PATH)
forge_interface = ForgeInterface(subprocess_handler)

# Update the AgentState if needed
class CodeState(AgentState):
    detected_issues: Optional[Union[List[str], str]] = None
    iteration_count: int = 0
    max_iterations: int = 5
    memory: dict = {}

# Define the code executor function
def code_executor(state: CodeState, forge_interface: ForgeInterface) -> dict:
    """
    Executes code edits using forge_interface.execute_subtask().
    """
    # Retrieve user profile from memory
    user_profile = retrieve_memory(('profile',), 'user_profile') or {}

    # Modify code changes based on user preferences (if any)
    code_changes = state.get('current_query', state['implementation_plan'])

    # Execute code changes using the provided forge_interface
    output = forge_interface.execute_subtask(code_changes)
    state['code_execution_output'] = output

    # Record code execution in memory
    if 'code_executions' not in state['memory']:
        state['memory']['code_executions'] = []
    state['memory']['code_executions'].append({
        'code_changes': code_changes,
        'output': output
    })

    # Add message
    state['messages'].append(AIMessage(content=f"Executed code changes:\n{code_changes}"))

    # Record experience
    record_experience(state)

    return state

# Define the code review function
def code_review(state: CodeState) -> dict:
    """
    Uses LLM to detect issues in the code by comparing original and modified files.
    """
    # Increment iteration count
    state['iteration_count'] += 1
    if state['iteration_count'] > state.get('max_iterations', 5):
        state['code_query_decision'] = 'END'
        state['code_query_explanation'] = 'Maximum iterations reached.'
        return state

    # Get the list of files to edit
    files_to_edit = state.get('files_to_edit', [])
    if not files_to_edit:
        state['messages'].append(SystemMessage(content="No files specified for editing."))
        state['code_query_decision'] = 'END'
        state['code_query_explanation'] = 'No files to edit.'
        return state

    # Read the original and modified files
    original_files_content = {}
    modified_files_content = {}
    for file_path in files_to_edit:
        try:
            # Read original file content (assuming backup exists)
            with open(f"{file_path}.backup", 'r') as f:
                original_files_content[file_path] = f.read()
            # Read modified file content
            with open(file_path, 'r') as f:
                modified_files_content[file_path] = f.read()
        except Exception as e:
            state['messages'].append(SystemMessage(content=f"Error reading files: {e}"))
            state['code_query_decision'] = 'END'
            state['code_query_explanation'] = 'Error reading files.'
            return state

    # Prepare the prompt for the LLM
    prompt = f"""
You are an expert software engineer reviewing code changes. Analyze the modifications between the original and modified files listed below, identify any potential issues, errors, or areas for improvement. Consider aspects such as syntax errors, logic errors, missing information, and compliance with best practices.

Provide a detailed analysis highlighting any detected issues and suggest improvements. If everything looks good, confirm that the changes are ready.

Output your response in JSON format with the following fields:
- issues_detected: A 'yes' or 'no' indicating whether issues were found.
- issues_list: A list of detected issues and suggestions for improvement. If none, write 'None'.
"""

    for file_path in files_to_edit:
        prompt += f"\nFile: {file_path}\n"
        prompt += f"Original Content:\n{original_files_content[file_path]}\n"
        prompt += f"Modified Content:\n{modified_files_content[file_path]}\n"

    # Define the schema for structured output
    class CodeReviewSchema(BaseModel):
        issues_detected: Literal['yes', 'no']
        issues_list: Union[List[str], str]

    # Invoke the LLM with structured output
    model_with_structure = llm.with_structured_output(CodeReviewSchema)
    try:
        structured_output = model_with_structure.invoke(prompt)
    except Exception as e:
        logging.error(f"LLM invocation failed: {e}")
        state['messages'].append(SystemMessage(content="An error occurred during code review."))
        state['code_query_decision'] = 'END'
        state['code_query_explanation'] = 'Error during LLM processing.'
        return state

    # Update state with the detected issues
    state['detected_issues'] = structured_output.issues_list

    # The linter node outputs what it thinks the error is
    state['messages'].append(SystemMessage(content=f"Detected issues: {structured_output.issues_list}"))

    # If issues were detected, proceed to generate a new query based on the errors
    if structured_output.issues_detected == 'yes':
        state['code_query_decision'] = 'Create Query'
    else:
        state['code_query_decision'] = 'Code Executor'
        state['messages'].append(SystemMessage(content="No issues detected in the code changes. Ready to proceed."))

    # Record the code review in memory
    if 'code_reviews' not in state['memory']:
        state['memory']['code_reviews'] = []
    state['memory']['code_reviews'].append({
        'files_reviewed': files_to_edit,
        'issues_detected': structured_output.issues_detected,
        'issues_list': structured_output.issues_list,
    })

    return state

# Define the create_query function
def create_query(state: CodeState) -> dict:
    """
    Formulates a new code change query based on the detected issues.
    """
    detected_issues = state.get('detected_issues', 'None')

    prompt = f"""
You are an expert software engineer tasked with formulating a new code change query to address the following detected issues:

Detected Issues:
{detected_issues}

Based on the issues above, generate a new implementation plan or code change instructions that will fix the problems.

Output your response in JSON format with the following field:
- query: The new code change query.
"""

    # Define the schema for structured output
    class CreateQuerySchema(BaseModel):
        query: str

    # Invoke the LLM with structured output
    model_with_structure = llm.with_structured_output(CreateQuerySchema)
    try:
        structured_output = model_with_structure.invoke(prompt)
    except Exception as e:
        logging.error(f"LLM invocation failed: {e}")
        state['messages'].append(SystemMessage(content="An error occurred during query creation."))
        state['code_query_decision'] = 'END'
        state['code_query_explanation'] = 'Error during LLM processing.'
        return state

    # Update the state with the new query
    state['current_query'] = structured_output.query
    state['messages'].append(SystemMessage(content=f"New query created based on detected issues: {structured_output.query}"))

    # Record the query in memory
    if 'queries' not in state['memory']:
        state['memory']['queries'] = []
    state['memory']['queries'].append(structured_output.query)

    # Decide to proceed to code executor
    state['code_query_decision'] = 'Code Executor'

    return state

# AWS Info Retrieval function
def aws_info_retrieval(state: CodeState) -> dict:
    """
    Retrieves AWS information based on the current query.
    """
    query = state.get('current_query')
    if not query:
        state['messages'].append(SystemMessage(content="No query provided for AWS Info Retrieval."))
        state['code_query_decision'] = 'END'
        state['code_query_explanation'] = 'No query provided.'
        return state

    # Implement the actual AWS retrieval logic here
    aws_info = retrieve_information(query)
    state['aws_info'] = aws_info
    state['messages'].append(SystemMessage(content=f"Retrieved AWS info: {aws_info}"))

    # Store AWS info in memory
    if 'aws_info' not in state['memory']:
        state['memory']['aws_info'] = []
    state['memory']['aws_info'].append({
        'query': query,
        'aws_info': aws_info
    })

    # After retrieving AWS info, proceed to create a new query
    state['code_query_decision'] = 'Create Query'

    return state

# User Info function
def user_info(state: CodeState) -> dict:
    """
    Generates a question to ask the user for advice based on the detected issues.
    """
    context = state.get('current_query', '')
    detected_issues = state.get('detected_issues', 'No specific issues detected.')

    prompt = f"""
        You are a helpful assistant for a AI software engineer. Based on the following context and detected issues, generate a concise and specific question to ask the user for advice on how to proceed.

        Context:
        {context}

        Detected Issues:
        {detected_issues}

        Your question should clearly state the issues and ask the user for their input or suggestions on resolving them.
"""

    try:
        question = llm.invoke(prompt).content.strip()
    except Exception as e:
        logging.error(f"LLM invocation failed: {e}")
        state['messages'].append(SystemMessage(content="An error occurred while generating a question for the user."))
        state['code_query_decision'] = 'END'
        state['code_query_explanation'] = 'Error during LLM processing.'
        return state

    # Present the question to the user and capture their response
    print(f"Assistant: {question}")
    user_response = input("User: ")
    state['user_response'] = user_response
    state['messages'].append(HumanMessage(content=user_response))

    # Store user response in memory
    if 'user_responses' not in state['memory']:
        state['memory']['user_responses'] = []
    state['memory']['user_responses'].append({
        'question': question,
        'response': user_response
    })

    # Update current_query with user response
    state['current_query'] = f"{state.get('current_query', '')}\nUser Response: {user_response}"

    # Decide to proceed to create a new query
    state['code_query_decision'] = 'Create Query'

    return state

# Define the code query function
def code_query(state: CodeState) -> dict:
    """
    Analyzes code execution output and decides the next steps.
    """
    output = state.get('code_execution_output', '')
    # Summarize past executions
    past_executions_summary = summarize_past_executions(state['memory'].get('code_executions', []))
    # Retrieve past decisions
    past_decisions = state['memory'].get('decisions', [])[-5:]
    # Retrieve user profile
    user_profile = retrieve_memory(('profile',), 'user_profile') or {}
    # Include detected issues and original implementation plan
    detected_issues = state.get('detected_issues', 'None')
    original_plan = state.get('implementation_plan', '')

    # Prepare the prompt
    prompt = f"""
You are an expert software engineer assisting a user.

User Profile:
{user_profile}

Original Implementation Plan:
{original_plan}

Detected Issues:
{detected_issues}

Past Executions:
{past_executions_summary}

Recent Decisions:
{past_decisions}

The latest code execution output is:

{output}

Analyze the output and the detected issues, and decide what the next step should be. Possible next steps are:
- 'END' if everything is successful or cannot proceed.
- 'AWS Info Retrieval' if more AWS data is needed.
- 'User Info' if more input from the user is required.
- 'Code Executor' to make additional code changes.
- 'Create Query' to formulate a new code change query based on the current context.

Provide your decision, and explain why you think this is the best next step, and what's wrong with the code if applicable.

Output your decision in JSON format with the fields:
- decision: one of 'END', 'AWS Info Retrieval', 'User Info', 'Code Executor', 'Create Query'.
- explanation: your reasoning.
"""

    # Define the schema for structured output
    class CodeQueryDecisionSchema(BaseModel):
        decision: Literal['END', 'AWS Info Retrieval', 'User Info', 'Code Executor', 'Create Query']
        explanation: str

    # Invoke the LLM with structured output
    model_with_structure = llm.with_structured_output(CodeQueryDecisionSchema)
    try:
        structured_output = model_with_structure.invoke(prompt)
    except Exception as e:
        logging.error(f"LLM invocation failed: {e}")
        state['messages'].append(SystemMessage(content="An error occurred during code query decision."))
        state['code_query_decision'] = 'END'
        state['code_query_explanation'] = 'Error during LLM processing.'
        return state

    # Update state with the decision
    state['code_query_decision'] = structured_output.decision
    state['code_query_explanation'] = structured_output.explanation
    state['messages'].append(SystemMessage(content=f"Code query decision: {structured_output.decision}. Explanation: {structured_output.explanation}"))

    # Record decision in memory
    if 'decisions' not in state['memory']:
        state['memory']['decisions'] = []
    state['memory']['decisions'].append({
        'decision': structured_output.decision,
        'explanation': structured_output.explanation,
    })

    # Record experience
    record_experience(state)

    return state

# Helper function to summarize past executions
def summarize_past_executions(past_executions):
    """
    Summarizes past code executions to keep the prompt concise.
    """
    if not past_executions:
        return "No past executions."
    # Summarize the last 5 executions
    summary = ""
    for idx, exec in enumerate(past_executions[-5:]):
        summary += f"Execution {idx+1}:\nChanges: {exec['code_changes']}\nOutput: {exec['output']}\n"
    return summary

# Function to record experiences in memory
def record_experience(state: CodeState):
    """
    Records experiences (actions and decisions) in memory.
    """
    if 'experiences' not in state['memory']:
        state['memory']['experiences'] = []
    experience = {
        'timestamp': datetime.now().isoformat(),
        'action': state.get('code_query_decision', ''),
        'details': state.get('code_query_explanation', ''),
        'code_changes': state.get('current_query', '')
    }
    state['memory']['experiences'].append(experience)
    # Optionally, store in persistent memory
    namespace = ('experiences',)
    key = f"experience_{experience['timestamp']}"
    store_memory(namespace, key, experience)

# Function to retrieve memory
def retrieve_memory(namespace: tuple, key: str) -> Optional[dict]:
    memory = store.get(namespace, key)
    if memory:
        return memory.value
    return None

# Function to store memory
def store_memory(namespace: tuple, key: str, value: dict):
    store.put(namespace, key, value)

# Function to trim messages
def trim_messages_in_state(state: CodeState, max_tokens: int = 4000):
    from langchain_core.messages import trim_messages
    trimmed = trim_messages(
        messages=state['messages'],
        strategy='last',
        max_tokens=max_tokens,
        token_counter=llm,
        start_on='human',
        end_on=('human', 'tool'),
        include_system=True,
    )
    state['messages'] = trimmed

# Function to prepare messages for LLM
def prepare_messages_for_llm(state: CodeState):
    """
    Prepares the messages by trimming or summarizing to fit the context window.
    """
    trim_messages_in_state(state, max_tokens=4000)

# Function to create the secondary agent workflow
def create_secondary_agent(forge_interface: ForgeInterface):
    workflow = StateGraph(CodeState)

    # Add nodes with forge_interface where needed
    workflow.add_node("prepare_messages_for_llm", prepare_messages_for_llm)
    workflow.add_node("code_review", code_review)
    workflow.add_node("code_executor", lambda state: code_executor(state, forge_interface))
    workflow.add_node("code_query", code_query)
    workflow.add_node("create_query", create_query)
    workflow.add_node("aws_info_retrieval", aws_info_retrieval)
    workflow.add_node("user_info", user_info)

    # Set entry point
    workflow.set_entry_point("prepare_messages_for_llm")

    # Edges
    workflow.add_edge("prepare_messages_for_llm", "code_review")
    workflow.add_edge("code_review", "code_query")
    workflow.add_edge("code_executor", "code_review")
    workflow.add_edge("create_query", "code_executor")

    # Conditional edges from 'code_query' based on 'code_query_decision'
    def decision_func(state):
        return state['code_query_decision']

    workflow.add_conditional_edges("code_query", decision_func, {
        "END": END,
        "AWS Info Retrieval": "aws_info_retrieval",
        "User Info": "user_info",
        "Code Executor": "code_executor",
        "Create Query": "create_query",
    })

    # After tool usage, go back to 'code_query'
    workflow.add_edge("aws_info_retrieval", "code_query")
    workflow.add_edge("user_info", "code_query")

    return workflow.compile()

# Function to start the secondary agent with the prior state
def start_secondary_agent(prior_state: AgentState, forge_interface: ForgeInterface):
    app = create_secondary_agent(forge_interface)
    initial_state = CodeState(prior_state)

    # Set default values for state variables
    initial_state['current_query'] = initial_state.get('implementation_plan', '')
    initial_state['iteration_count'] = 0
    initial_state['max_iterations'] = 5

    # Ensure 'memory' is a dictionary
    if 'memory' not in initial_state or not isinstance(initial_state['memory'], dict):
        initial_state['memory'] = {}

    # Initialize memory components with default empty lists
    memory_keys = [
        'code_executions', 'code_reviews', 'queries', 'aws_info',
        'user_responses', 'decisions', 'experiences'
    ]
    for key in memory_keys:
        initial_state['memory'].setdefault(key, [])

    # Start the workflow
    for event in app.stream(initial_state):
        print(event)

    return initial_state

def start_code_loop(state_from_setup: AgentState, forge_interface: ForgeInterface):
    final_state = start_secondary_agent(state_from_setup, forge_interface)
    return final_state
