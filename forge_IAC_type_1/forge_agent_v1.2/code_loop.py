# secondary_agent.py

from langgraph.graph import StateGraph, END
from utils.git_utils import clone_repository, create_combined_txt
from utils.workflow_utils import execute_parallel_tasks, configure_aws_from_env
from typing import TypedDict, List, Union, Optional, Annotated
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, SystemMessage, RemoveMessage
import os
import logging
from langgraph.graph import add_messages
from typing import Literal
from utils.workflow_utils import setup_AWS_state, generate_file_descriptions, generate_codebase_overview
from rag_agent import choose_relevant_IaC_files, choose_relevant_aws_files, retrieve_information
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field
import asyncio
from pathlib import Path
from datetime import datetime
from langgraph.store.memory import InMemoryStore  # Use a persistent store in production
from setup_agent import AgentState
# Initialize the memory store
store = InMemoryStore()

# Setup logging
logging.basicConfig(level=logging.INFO)
llm = ChatOpenAI(model="gpt-4", temperature=0)

class CodeState(TypedDict):
    messages: Annotated[List[BaseMessage], add_messages]  # Short-term memory
    query: str
    repo_path: str
    combined_file_path: str
    aws_identity: str
    file_descriptions: dict
    file_tree: str
    codebase_overview: str
    edit_code_decision: str
    files_to_edit: List[str]
    implementation_plan: str
    execute_commands: str
    aws_info_query: str
    info_retrieval_query: str
    retrieve_info: str
    code_execution_output: str
    code_query_decision: str
    code_query_explanation: str
    current_query: str
    aws_info: Optional[str]
    user_response: Optional[str]
    memory: dict  # Long-term memory

# Define the code executor function
def code_executor(state: AgentState) -> dict:
    """
    Executes code edits using forge_interface.execute_subtask().
    """
    # Retrieve user profile from memory
    user_profile = retrieve_memory(('profile',), 'user_profile') or {}
    # Modify code changes based on user preferences (if any)
    code_changes = state.get('current_query', state['implementation_plan'])
    # Example adjustment based on user preference (not implemented here)
    # code_changes = adapt_code_style(code_changes, user_profile.get('preferred_language'))

    # Execute code changes
    output = forge_interface.execute_subtask(code_changes)
    state['code_execution_output'] = output
    state['messages'].append(AIMessage(content=f"Executed code changes:\n{code_changes}"))
    # Record experience
    record_experience(state)
    return state

# Define the code query function
def code_query(state: AgentState) -> dict:
    """
    Analyzes code execution output and decides the next steps.
    """
    output = state['code_execution_output']
    # Summarize past executions
    past_executions_summary = summarize_past_executions(state['memory']['code_executions'])
    # Retrieve past decisions
    past_decisions = state['memory']['decisions'][-5:]
    # Retrieve user profile
    user_profile = retrieve_memory(('profile',), 'user_profile') or {}

    # Prepare the prompt
    prompt = f"""
You are an expert software engineer assisting a user.

User Profile:
{user_profile}

Past Executions:
{past_executions_summary}

Recent Decisions:
{past_decisions}

The latest code execution output is:

{output}

Analyze the output, and decide what the next step should be. Possible next steps are:
- 'END' if everything is successful.
- 'AWS Info Retrieval' if more AWS data is needed.
- 'User Info' if more input from the user is required.
- 'Code Executor' to make additional code changes.
- 'Create Query' to formulate a new code change query.

Provide your decision, and explain why you think this is the best next step, and what's wrong with the code if applicable.

Output your decision in JSON format with the fields:
- decision: one of 'END', 'AWS Info Retrieval', 'User Info', 'Code Executor', 'Create Query'.
- explanation: your reasoning.
- query: the new query if you decide to create another query.
"""

    # Define the schema for structured output
    class CodeQueryDecisionSchema(BaseModel):
        decision: Literal['END', 'AWS Info Retrieval', 'User Info', 'Code Executor', 'Create Query']
        explanation: str
        query: Optional[str]

    # Invoke the LLM with structured output
    model_with_structure = llm.with_structured_output(CodeQueryDecisionSchema)
    structured_output = model_with_structure.invoke(prompt)

    # Update state with the decision
    state['code_query_decision'] = structured_output.decision
    state['code_query_explanation'] = structured_output.explanation
    if structured_output.query:
        state['current_query'] = structured_output.query
    state['messages'].append(SystemMessage(content=f"Code query decision: {structured_output.decision}. Explanation: {structured_output.explanation}"))

    # Record decision in memory
    state['memory']['decisions'].append({
        'decision': structured_output.decision,
        'explanation': structured_output.explanation,
        'query': structured_output.query
    })

    # Record experience
    record_experience(state)

    return state

# AWS Info Retrieval function
def aws_info_retrieval(state: AgentState) -> dict:
    """
    Retrieves AWS information based on the current query.
    """
    query = state.get('current_query')
    if not query:
        state['messages'].append(SystemMessage(content="No query provided for AWS Info Retrieval."))
        return state

    # Implement the actual AWS retrieval logic here
    aws_info = retrieve_information(query)
    state['aws_info'] = aws_info
    state['messages'].append(SystemMessage(content=f"Retrieved AWS info: {aws_info}"))

    # Store AWS info in memory
    state['memory']['aws_info'].append({
        'query': query,
        'aws_info': aws_info
    })
    return state

# User Info function
def user_info(state: AgentState) -> dict:
    """
    Generates a question to ask the user for more information.
    """
    context = state.get('current_query', '')
    prompt = f"""
You are a helpful assistant. Based on the following context, generate a concise question to ask the user to obtain more information:

Context:
{context}
"""
    question = llm.invoke(prompt).content.strip()
    user_response = input(f"{question}\n> ")
    state['user_response'] = user_response
    state['messages'].append(HumanMessage(content=user_response))

    # Store user response in memory
    namespace = ('responses',)
    key = f"response_{datetime.now().isoformat()}"
    store_memory(namespace, key, {'question': question, 'response': user_response})

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
def record_experience(state: AgentState):
    """
    Records experiences (actions and decisions) in memory.
    """
    namespace = ('experiences',)
    experience = {
        'timestamp': datetime.now().isoformat(),
        'action': state.get('code_query_decision', ''),
        'details': state.get('code_query_explanation', ''),
        'code_changes': state.get('current_query', '')
    }
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
def trim_messages_in_state(state: AgentState, max_tokens: int = 4000):
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
def prepare_messages_for_llm(state: AgentState):
    """
    Prepares the messages by trimming or summarizing to fit the context window.
    """
    trim_messages_in_state(state, max_tokens=4000)

# Function to start the secondary agent
def create_secondary_agent():
    workflow = StateGraph(AgentState)

    # Add nodes
    workflow.add_node("prepare_messages_for_llm", prepare_messages_for_llm)
    workflow.add_node("code_executor", code_executor)
    workflow.add_node("code_query", code_query)
    workflow.add_node("aws_info_retrieval", aws_info_retrieval)
    workflow.add_node("user_info", user_info)

    # Set entry point
    workflow.set_entry_point("prepare_messages_for_llm")

    # Edges
    workflow.add_edge("prepare_messages_for_llm", "code_executor")
    workflow.add_edge("code_executor", "code_query")

    # Conditional edges from 'code_query' based on 'code_query_decision'
    def decision_func(state):
        return state['code_query_decision']

    workflow.add_conditional_edges("code_query", decision_func, {
        "END": END,
        "AWS Info Retrieval": "aws_info_retrieval",
        "User Info": "user_info",
        "Code Executor": "code_executor",
        "Create Query": "code_executor",
    })

    # After tool usage, go back to 'code_query'
    workflow.add_edge("aws_info_retrieval", "code_query")
    workflow.add_edge("user_info", "code_query")

    return workflow.compile()

# Function to start the secondary agent with the prior state
def start_secondary_agent(prior_state: AgentState):
    app = create_secondary_agent()
    initial_state = prior_state.copy()
    # Set 'current_query' to 'implementation_plan' to start
    initial_state['current_query'] = initial_state['implementation_plan']
    # Initialize memory
    initial_state['memory'] = {
        'code_executions': [],
        'queries': [],
        'aws_info': [],
        'user_responses': [],
        'decisions': []
    }

    # Start the workflow
    for event in app.stream(initial_state):
        print(event)

    return initial_state


def start_code_loop(state_from_setup: AgentState):
    initial_state = CodeState({
            "messages": [],
            "query": "",
            "repo_path": os.getenv("REPO_PATH"),
            "combined_file_path": "",
            "aws_identity": "",
            "file_descriptions": {},
            "codebase_overview": "",
            "edit_code_decision": "yes",
            "files_to_edit": [],
            "implementation_plan": "",
            "file_tree": "",
            "execute_commands": "",
            "aws_info_query": "",
            "info_retrieval_query": "",
            "retrieve_info": "",
            "code_execution_output": "",
            "code_query_decision": "",
            "code_query_explanation": "",
            "current_query": "",
            "aws_info": None,
            "user_response": None,
            "memory": {},
        })
    
    for key, value in state_from_setup.items():
        initial_state[key] = value

    final_state = start_secondary_agent(initial_state)


    return final_state
