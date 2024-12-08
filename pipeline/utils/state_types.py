from typing import TypedDict, List, Union, Optional
from langchain_core.messages import BaseMessage

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
    repo_path: str
    current_query: Optional[str]
    resolution_input: Optional[str]
    user_input_needed: Optional[str]
    rag_query: Optional[str]

class ExecutionState(TypedDict):
    messages: List[BaseMessage]
    current_query: Optional[str]
    detected_issues: Optional[Union[List[str], str]]
    iteration_count: int
    max_iterations: int
    memory: dict
    implementation_plan: str
    code_execution_output: Optional[str]
    code_query_decision: Optional[str]
    code_query_explanation: Optional[str]
    relevant_files: List[str]
    rag_query: Optional[str]
    rag_response: Optional[str]
    info_needed: Optional[str]
    linting_result: Optional[dict]
    user_input_needed: Optional[str]
    validation_ready: bool
    repo_path: str 