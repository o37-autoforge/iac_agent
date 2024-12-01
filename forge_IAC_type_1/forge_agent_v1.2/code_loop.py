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
from pathlib import PosixPath, Path
import asyncio
# Initialize the memory store
store = InMemoryStore()

# Setup logging
logging.basicConfig(level=logging.INFO)
llm = ChatOpenAI(model="gpt-4o", temperature=0)
from utils.subprocess_handler import SubprocessHandler
from utils.forge_interface import ForgeInterface
REPO_PATH = os.getenv("REPO_PATH")

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

    code_changes = state.get('current_query', state.get('implementation_plan', ''))

    if not code_changes:
        state['messages'].append(SystemMessage(content="No code changes to execute."))
        state['code_query_decision'] = 'END'
        state['code_query_explanation'] = 'No code changes provided.'
        return state

    logging.info(f"Executing code changes: {code_changes}")
    output = asyncio.run(forge_interface.execute_subtask(code_changes))
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
        # If it's the first iteration and files_to_edit is empty, proceed without error
        if state['iteration_count'] == 1:
            state['messages'].append(SystemMessage(content="No files specified for editing on first iteration."))
            state['code_query_decision'] = 'Code Executor'
            state['code_query_explanation'] = 'Proceeding to execute initial code changes.'
            return state
        else:
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
        state['code_query_decision'] = 'END'
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
You are a helpful assistant for an AI software engineer. Based on the following context and detected issues, generate a concise and specific question to ask the user for advice on how to proceed.

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

                **Original Implementation Plan:**
                {original_plan}

                **Detected Issues (if any):**
                {detected_issues}

                Past Executions (if any):
                {past_executions_summary}

                Recent Decisions (if any):
                {past_decisions}

                The latest code execution output is:
                {output}

                Analyze the output and the detected issues, and decide what the next step should be. Possible next steps are:
                - 'END' if everything is successful or cannot proceed.
                - 'AWS Info Retrieval' if more AWS data is needed. For example, if the code changes require AWS resources that are not currently defined in the codebase.
                - 'User Info' if more input from the user is required. For example, if we cannot solve the problem ourselves for more than 2 iterations.
                - 'Create Query' to formulate a new code change query based on the current context. For example, if we dont need to consult AWS or the user for more information. 

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
    workflow.add_edge("prepare_messages_for_llm", "code_executor")  
    workflow.add_edge("code_executor", "code_review")
    workflow.add_edge("code_review", "code_query")
    workflow.add_edge("create_query", "code_executor")

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
    # Ensure prior_state has 'implementation_plan'
    assert 'implementation_plan' in prior_state, "implementation_plan is missing in prior_state"

    app = create_secondary_agent(forge_interface)
    initial_state = CodeState(prior_state)

    # Set default values for state variables
    initial_state['current_query'] = initial_state.get('implementation_plan', '')
    initial_state['iteration_count'] = 0
    initial_state['max_iterations'] = initial_state.get('max_iterations', 5)

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

    # Ensure messages list exists
    if 'messages' not in initial_state:
        initial_state['messages'] = []

    # Start the workflow
    final_state = app.invoke(initial_state)

    print(final_state)
    return final_state

def start_code_loop(state_from_setup: AgentState, forge_interface: ForgeInterface):
    final_state = start_secondary_agent(state_from_setup, forge_interface)
    return final_state


if __name__ == "__main__":
    state = {'messages': [SystemMessage(content='Initialized repository and configured AWS.', additional_kwargs={}, response_metadata={}, id='74defa9c-0596-4055-a61c-964e6a75e8e8'), SystemMessage(content='Initialized repository and configured AWS.', additional_kwargs={}, response_metadata={}, id='fdcc36d3-3ebc-4395-9765-73747dfcc99e'), SystemMessage(content='Files combined into a single text file.', additional_kwargs={}, response_metadata={}, id='22fe2772-23c1-4f41-88e5-18741ff844cb'), SystemMessage(content='Files combined into a single text file.', additional_kwargs={}, response_metadata={}, id='d075b72e-2f18-49fb-879f-36776f98e33d'), SystemMessage(content='Generated file tree of the repository.', additional_kwargs={}, response_metadata={}, id='dbd69ed6-06c9-4ef2-b16a-359005d8f75e'), SystemMessage(content='Generated file tree of the repository.', additional_kwargs={}, response_metadata={}, id='9ce37c52-b34e-42e7-ab16-2459a32eec98'), SystemMessage(content='AWS Raggable data tree created.', additional_kwargs={}, response_metadata={}, id='b34a976c-daf3-48ed-b681-496792a21b01'), SystemMessage(content='AWS Raggable data tree created.', additional_kwargs={}, response_metadata={}, id='cd89277a-a2d0-4632-b037-9d3d98f13a18'), SystemMessage(content='Generated file tree of the repository.', additional_kwargs={}, response_metadata={}, id='a3ab2382-8509-455f-8ca0-4d9a566a4437'), SystemMessage(content='Generated a natural language overview of the codebase.', additional_kwargs={}, response_metadata={}, id='f82c5b9e-c1ee-4102-8615-a34fd8d18d6c'), SystemMessage(content='Generated file tree of the repository.', additional_kwargs={}, response_metadata={}, id='0e2eef08-9376-4220-bd4e-f4bc4b2a7828'), SystemMessage(content='Generated a natural language overview of the codebase.', additional_kwargs={}, response_metadata={}, id='e69ff1bf-4102-4dda-b8c6-c039eca2469f'), SystemMessage(content='Generated natural language descriptions for each code file.', additional_kwargs={}, response_metadata={}, id='fa64ddad-8b37-492e-aa69-4a690d815b6b'), SystemMessage(content='Generated natural language descriptions for each code file.', additional_kwargs={}, response_metadata={}, id='95f8faca-030b-419a-87f7-63734dce07b5'), HumanMessage(content='add another s3 bucket', additional_kwargs={}, response_metadata={}, id='2ea06c7a-61fe-4ad7-8cd7-20fbfd5f4ada'), HumanMessage(content='add another s3 bucket', additional_kwargs={}, response_metadata={}, id='3c074985-9ea5-4eb1-8244-ec210ffac37b'), SystemMessage(content='Edit decision: yes.', additional_kwargs={}, response_metadata={}, id='186b0151-4e90-4193-8b80-5d9abd1bf42b'), SystemMessage(content='Edit decision: yes.', additional_kwargs={}, response_metadata={}, id='dc6235b8-64b1-45f6-91d6-3a18a5f2cd31'), SystemMessage(content="Files to edit: ['main.tf', 'outputs.tf', 'variables.tf'].", additional_kwargs={}, response_metadata={}, id='f840fdd2-1b7f-4f97-bef7-e1a0477ac15c'), SystemMessage(content="Files to edit: ['main.tf', 'outputs.tf', 'variables.tf'].", additional_kwargs={}, response_metadata={}, id='5be64af2-6962-4f7d-b64a-b73e000bda28'), SystemMessage(content='Command execution decision: no.', additional_kwargs={}, response_metadata={}, id='5bf67b61-9836-4df8-a49a-db3790d63fa1'), SystemMessage(content='Command execution decision: no.', additional_kwargs={}, response_metadata={}, id='90902ab4-38f8-4045-9582-521aa170b73d'), SystemMessage(content='Information retrieval decision: yes.', additional_kwargs={}, response_metadata={}, id='0b8299a9-e1dc-4b08-9728-4af23dde0eea'), SystemMessage(content='Information retrieval decision: yes.', additional_kwargs={}, response_metadata={}, id='c5f6f6dc-9fc9-4dce-828b-6561f6e2bf24'), SystemMessage(content='Info retrieval query: What is the Terraform version required in the codebase?.', additional_kwargs={}, response_metadata={}, id='e3da6586-7f65-4a77-b544-49e8f023840c'), SystemMessage(content='Info retrieval query: What is the Terraform version required in the codebase?.', additional_kwargs={}, response_metadata={}, id='44087633-640c-4ab7-b69d-2a796d4e205c'), SystemMessage(content='Implementation plan created.', additional_kwargs={}, response_metadata={}, id='d8582098-c04a-44ba-a4ba-3adafa5ed1bc'), SystemMessage(content='Implementation plan created.', additional_kwargs={}, response_metadata={}, id='9e5d009c-d899-4ace-8b6c-8b74ead8b6c7')], 'query': '', 'repo_path': '/Users/rkala/Documents/GitHub/iac_agent/forge_IAC_type_1/forge_agent_v1.2/repo', 'combined_file_path': PosixPath('/Users/rkala/Documents/GitHub/iac_agent/forge_IAC_type_1/forge_agent_v1.2/rag/cb_combined.txt'), 'aws_identity': 'arn:aws:iam::980921723213:root', 'file_descriptions': {'/Users/rkala/Documents/GitHub/iac_agent/forge_IAC_type_1/forge_agent_v1.2/repo/outputs.tf': 'This Infrastructure as Code (IaC) snippet is written in HashiCorp Configuration Language (HCL), which is commonly used with Terraform. The code defines an output variable named "public_dns." This output variable is configured to retrieve and display the public DNS address of an AWS EC2 instance that is identified by the resource name "ubuntu." The value of this output is dynamically obtained from the `public_dns` attribute of the specified AWS instance. When you apply this Terraform configuration, it will output the public DNS of the specified EC2 instance, allowing you to easily access or reference it elsewhere.', '/Users/rkala/Documents/GitHub/iac_agent/forge_IAC_type_1/forge_agent_v1.2/repo/main.tf': 'This Infrastructure as Code (IaC) file is written in HashiCorp Configuration Language (HCL) for use with Terraform. It describes the configuration for deploying resources on Amazon Web Services (AWS). Here\'s a breakdown of what each part of the file does:\n\n1. **Terraform Configuration:**\n   - The `terraform` block specifies that the required version of Terraform to use this configuration is 0.11.0 or higher.\n\n2. **AWS S3 Bucket Resource:**\n   - A resource of type `aws_s3_bucket` is defined with the name "LindsaysBucket".\n   - The bucket is named "lindsays-bucket" and is set to have a private access control list (ACL), meaning it is not publicly accessible.\n   - Versioning is configured for the bucket, but it is disabled (`enabled = false`).\n\n3. **AWS S3 Bucket Public Access Block:**\n   - A resource of type `aws_s3_bucket_public_access_block` is associated with the previously defined S3 bucket "LindsaysBucket".\n   - The configuration does not block public ACLs or public policies, and it does not ignore public ACLs or restrict public buckets. This means that public access is not explicitly blocked, but the bucket itself is private due to its ACL setting.\n\n4. **AWS Provider Configuration:**\n   - The `provider` block specifies the AWS provider and sets the region using a variable `${var.aws_region}`. This means the region is dynamically set based on the value of the `aws_region` variable.\n\n5. **AWS EC2 Instance Resource:**\n   - A resource of type `aws_instance` is defined with the name "ubuntu".\n   - The Amazon Machine Image (AMI) ID and instance type are specified using variables `${var.ami_id}` and `${var.instance_type}`, respectively.\n   - The instance is launched in a specific availability zone, which is the value of `${var.aws_region}` with an appended "a" (e.g., if `aws_region` is "us-west-2", the instance will be in "us-west-2a").\n   - The instance is tagged with a name, which is set using the variable `${var.name}`.\n\nOverall, this Terraform configuration sets up an AWS S3 bucket with specific access settings and an EC2 instance with customizable parameters based on input variables.', '/Users/rkala/Documents/GitHub/iac_agent/forge_IAC_type_1/forge_agent_v1.2/repo/variables.tf': 'This Infrastructure as Code (IaC) file is written in a format commonly used by Terraform, a tool for building, changing, and versioning infrastructure safely and efficiently. The file defines four variables that are used to configure the provisioning of an AWS EC2 instance. Here\'s a breakdown of each variable:\n\n1. **aws_region**: This variable specifies the AWS region where the resources will be provisioned. It has a description "AWS region" and a default value set to "us-west-1", which is the US West (N. California) region.\n\n2. **ami_id**: This variable holds the ID of the Amazon Machine Image (AMI) that will be used to launch the EC2 instance. The description indicates that the default AMI is an "Ubuntu 14.04 Base Image," and the default value is set to "ami-05c65d8bb2e35991a".\n\n3. **instance_type**: This variable defines the type of EC2 instance to be provisioned. It has a description "type of EC2 instance to provision" and defaults to "t2.micro", which is a small, cost-effective instance type suitable for low-traffic applications and testing.\n\n4. **name**: This variable is used to assign a name to the EC2 instance by passing it to the Name tag. The description is "name to pass to Name tag," and the default value is "Provisioned by Terraform".\n\nThese variables allow for flexible configuration of the EC2 instance, enabling users to customize the region, AMI, instance type, and name as needed.'}, 'file_tree': 'repo/\n    terraform.tfstate.backup\n    plan.tfplan\n    outputs.tf\n    terraform.tfstate\n    tfplan\n    main.tf\n    README.md\n    .gitignore\n    variables.tf\n    .terraform.lock.hcl\n    .git/\n        ORIG_HEAD\n        config\n        HEAD\n        description\n        index\n        packed-refs\n        FETCH_HEAD\n        objects/\n            pack/\n                pack-0985c34fa57999a8aa49c397984c1fe2301049f3.pack\n                pack-0985c34fa57999a8aa49c397984c1fe2301049f3.idx\n            info/\n        info/\n            exclude\n        logs/\n            HEAD\n            refs/\n                heads/\n                    test\n                remotes/\n                    origin/\n                        HEAD\n        hooks/\n            commit-msg.sample\n            pre-rebase.sample\n            pre-commit.sample\n            applypatch-msg.sample\n            fsmonitor-watchman.sample\n            pre-receive.sample\n            prepare-commit-msg.sample\n            post-update.sample\n            pre-merge-commit.sample\n            pre-applypatch.sample\n            pre-push.sample\n            update.sample\n            push-to-checkout.sample\n        refs/\n            heads/\n                test\n            tags/\n            remotes/\n                origin/\n                    HEAD\n    forge_logs/\n        status_log.txt', 'codebase_overview': 'This codebase uses Terraform to provision an AWS EC2 t2.micro instance and an S3 bucket. It also includes supporting files for version control, documentation, and dependency management.\n\n**File Types and Purposes:**\n\n* **`.tf` (Terraform Configuration Files):** These files define the infrastructure to be provisioned.\n    * `main.tf`: This is the primary configuration file and contains the resources to be created (EC2 instance and S3 bucket), the AWS provider configuration, and references to variables.\n    * `variables.tf`: This file defines the variables used in `main.tf`, such as the AWS region, AMI ID, instance type, and name tag.\n    * `outputs.tf`:  Defines the outputs from the Terraform execution, specifically the public DNS of the created EC2 instance.\n* **`.tfstate` (Terraform State Files):** These files store the state of the deployed infrastructure.  `terraform.tfstate` is the current state, and `terraform.tfstate.backup` is a backup.\n* **`.terraform.lock.hcl` (Terraform Lock File):**  Specifies the exact versions of the Terraform providers used in the project, ensuring consistent deployments across different environments.\n* **`README.md` (Markdown File):** This file provides documentation for the project, explaining its purpose, how to use it, and any prerequisites.\n* **`.gitignore` (Git Ignore File):** This file specifies files and directories that should be ignored by Git, the version control system. This helps keep the repository clean and prevents sensitive or unnecessary files from being tracked.\n* **`.git` (Git Directory):** This hidden directory contains the Git repository metadata and history.  The files within track changes, branches, remote repositories, and other configuration information.\n* **`forge_logs/status_log.txt`: (Forge Status Log):** This file seems to be a log from a tool called "Forge." It records the steps taken during code analysis, implementation, testing, deployment, and Git operations.\n\n**File Tree:**\n\n```\n.\n├── .git\n│   ├── ... (Git repository files)\n├── .gitignore\n├── .terraform.lock.hcl\n├── forge_logs\n│   └── status_log.txt\n├── main.tf\n├── outputs.tf\n├── README.md\n├── terraform.tfstate\n├── terraform.tfstate.backup\n└── variables.tf\n```\n\n**Codebase Overview and Purpose:**\n\nThe main purpose of this Terraform code is to automate the provisioning of an EC2 instance and an S3 bucket on AWS. The EC2 instance is configured with a specified AMI, instance type, availability zone, and name tag. The S3 bucket is set up with private access control.  The `outputs.tf` file makes it easy to access the public DNS of the EC2 instance after it\'s created. The `variables.tf` file allows for customization of the deployment. The README file provides instructions for users, including the need to set AWS credentials. The `.gitignore` file ensures that local configuration files and state files are not committed to the repository. The `status_log.txt` file indicates the involvement of an external tool or service (Forge) that likely orchestrated the Terraform workflows, including deployments and Git operations.  The state files record the current infrastructure managed by Terraform, while the lock file helps maintain consistent provider versions.', 'edit_code_decision': 'yes', 'files_to_edit': ['main.tf', 'outputs.tf', 'variables.tf'], 'implementation_plan': '### Implementation Plan for User Query: Create a Virtual Machine on AWS\n\n#### Step 1: Update the `main.tf` File\n1. **Open `main.tf`**: This file contains the main configuration for Terraform.\n2. **Modify the `aws_instance` Resource**:\n   - Ensure the `aws_instance` resource is configured to create a virtual machine (VM) on AWS.\n   - Update the `ami` and `instance_type` to match the user\'s requirements for a small-scale infrastructure.\n   - Set the `availability_zone` to `${var.aws_region}a` as per the user\'s preference for a specific region.\n   - Ensure the `tags` block follows the naming convention `project-name-resource-type`.\n\n#### Step 2: Update the `variables.tf` File\n1. **Open `variables.tf`**: This file defines the variables used in the Terraform configuration.\n2. **Ensure Variables are Defined**:\n   - `aws_region`: Already defined with a default of `us-west-1`. Confirm this is the desired region or update as needed.\n   - `ami_id`: Ensure this is set to a valid AMI ID for the desired operating system (e.g., Ubuntu).\n   - `instance_type`: Ensure this is set to `t2.micro` for a small-scale instance.\n   - `name`: Update the default value to follow the naming convention `project-name-resource-type`.\n\n#### Step 3: Validate the Configuration\n1. **Run `terraform validate`**: Ensure the configuration is syntactically valid.\n2. **Check for Errors**: Address any errors or warnings that arise during validation.\n\n#### Step 4: Plan the Infrastructure Changes\n1. **Run `terraform plan`**: Generate an execution plan to see what changes Terraform will make.\n2. **Review the Plan**: Ensure the plan aligns with the user\'s requirements and preferences.\n\n#### Step 5: Apply the Configuration\n1. **Run `terraform apply`**: Apply the changes required to reach the desired state of the configuration.\n2. **Confirm the Apply**: If prompted, confirm the apply operation.\n\n#### Step 6: Verify the Deployment\n1. **Check AWS Console**: Verify that the virtual machine is created in the specified region and availability zone.\n2. **Verify Tags**: Ensure the VM is tagged correctly following the naming convention.\n\n#### Step 7: Document the Changes\n1. **Update `README.md`**: Document the changes made, including the new VM creation and any specific configurations.\n2. **Commit Changes**: Use Git to commit the changes to the repository.\n   - Run `git add .`\n   - Run `git commit -m "Add VM creation configuration for AWS"`\n   - Run `git push` to push the changes to the remote repository.\n\nThis plan ensures the creation of a virtual machine on AWS, following the user\'s preferences and the existing codebase structure.', 'execute_commands': 'no', 'aws_info_query': '', 'info_retrieval_query': 'What is the Terraform version required in the codebase?', 'retrieve_info': 'yes', 'user_questions': '[\n  {\n    "question": "What type of infrastructure resource do you want to create or manage?",\n    "response": "virtual machine"\n  },\n  {\n    "question": "Which cloud provider should the infrastructure be deployed to?",\n    "response": "AWS"\n  },\n  {\n    "question": "Do you have any specific region or availability zone preferences for deployment?",\n    "response": "y"\n  },\n  {\n    "question": "What is the expected scale or size of the infrastructure?",\n    "response": "small"\n  },\n  {\n    "question": "Are there any specific security or compliance requirements?",\n    "response": "No specific requirements"\n  },\n  {\n    "question": "Do you have any existing VPC or network configurations to integrate with?",\n    "response": "No existing VPC"\n  },\n  {\n    "question": "What is the budget or cost constraints for this infrastructure?",\n    "response": "No specific budget constraints"\n  },\n  {\n    "question": "Do you have any naming conventions for resources?",\n    "response": "project-name-resource-type"\n  }\n]', 'memory': {}}
    subprocess_handler = SubprocessHandler(Path(os.getenv("REPO_PATH")))
    forge_interface = ForgeInterface(subprocess_handler)
    subprocess_handler.start_forge(os.getenv("OPENAI_API_KEY"), state["files_to_edit"])
    start_code_loop(state, forge_interface)