from langgraph.graph import StateGraph, END
from typing import TypedDict, Annotated, List, Dict, Any, Literal, Optional
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel
from datetime import datetime
import logging
import os
import asyncio
import json
from pathlib import Path
from utils.rag_utils import RAGUtils
from utils.state_utils import append_state_update
from pydantic import Field

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
# Disable all logging levels
logging.disable(logging.CRITICAL)
logger = logging.getLogger(__name__)

# Initialize LLM
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
    files_to_edit: list[str]
    validation_ready: bool
    current_files: dict  # Current state of edited files
    code_query_decision: str  # Next node to execute
    rag_utils: Optional[RAGUtils]

async def run_forge_subtask(forge_interface, query: str) -> str:
    return await forge_interface.execute_subtask(query)

def code_executor(state: ExecutionState) -> ExecutionState:
    """Execute code changes based on the current implementation_plan."""
    logger.info("Starting code execution")
    append_state_update(state["repo_path"], "execution", "code_executor", "Executing infrastructure code changes")
    
    # Get user inputs and current issue from memory
    user_inputs = state["memory"].get('user_inputs', [])
    current_issue = state["memory"].get('current_issue')
    forge = state["memory"].get('forge')
    
    if not forge:
        logger.error("No forge instance available")
        state["code_query_decision"] = "end"
        return state
    
    # Check if this exact query has been sent before
    previous_queries = state["memory"].get('previous_queries', set())
    
    # Build query based on iteration count
    if state["iteration_count"] == 1:
        query = f"""
        As an Infrastructure as Code expert, implement the following implementation plan.
        Follow these strict guidelines:
        1. Make changes ONLY to the files mentioned in the implementation plan
        2. Follow IaC best practices for security and configuration
        3. Use these specific values from user input:
        {json.dumps(user_inputs, indent=2) if user_inputs else "No user inputs yet"}

        Implementation Plan:
        {state["implementation_plan"]}
        """
    else:
        # For subsequent iterations, prioritize the current issue
        query = f"""
        As an Infrastructure as Code expert, fix the following issue in the infrastructure code:

        Issue to Fix:
        {current_issue}

        Available User Inputs:
        {json.dumps(user_inputs, indent=2) if user_inputs else "No user inputs available"}

        Original Implementation Plan:
        {state["implementation_plan"]}

        Focus on addressing the identified issue first, using any available information from user inputs.
        """
    
    # Check if this query has been sent before
    if query in previous_queries:
        logger.warning("Duplicate query detected, skipping execution")
        state["code_query_decision"] = "review"
        return state
    
    # Add query to previous queries
    previous_queries.add(query)
    state["memory"]['previous_queries'] = previous_queries
    
    logger.info(f"Sending query to forge:\n{query}")
    
    try:
        # Get clean response and file updates
        clean_response, edited_files = forge.chat_and_get_updates(query)
        
        # Store the edited files in state
        state["current_files"] = edited_files
        state["code_execution_output"] = clean_response
        state["code_query_decision"] = "review"
        
        state["messages"] = manage_messages(
            state["messages"], 
            SystemMessage(content=f"Code execution completed: {clean_response[:100]}...")
        )
        
        if 'code_executions' not in state["memory"]:
            state["memory"]['code_executions'] = []
        state["memory"]['code_executions'].append({
            'query': query,
            'output': clean_response,
            'edited_files': list(edited_files.keys()),
            'iteration': state["iteration_count"],
            'current_issue': current_issue,
            'user_inputs': user_inputs,
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

def code_reviewer(state: ExecutionState) -> ExecutionState:
    """Review code changes for correctness and completeness."""
    logger.info("Starting code review")
    append_state_update(state["repo_path"], "execution", "code_reviewer", "Reviewing infrastructure code changes")
    
    # Get files that need to be reviewed
    files_to_review = state["files_to_edit"]

    if not files_to_review:
        logger.warning("No files marked for review!")
        state["code_query_decision"] = "user"
        state["memory"]['current_user_prompt'] = "Please provide the current contents of the infrastructure code files to review."
        return state
        
    # Get past user responses from memory
    past_responses = state["memory"].get('user_inputs', [])
    past_responses_str = "\n".join([
        f"Q: {resp['prompt']}\nA: {resp['response']}"
        for resp in past_responses
    ])

    filecontents = ""
    for file_path in files_to_review:
        content = open(state['repo_path'] + "/" + file_path, 'r').read()
        filecontents += f"\n\nFile: {file_path}\n```hcl\n{content}\n```"
    
    # print(filecontents)

    prompt = f"""
    Review the following infrastructure code changes:

    Implementation Plan: {state["implementation_plan"]}

    Files being reviewed: {filecontents}

    Previous User Responses:
    {past_responses_str if past_responses else "No previous responses"}
    
    Make a decision about the code changes:
    - READY: Code is complete and correct
    - FIX: Code needs syntax fixes (provide details)
    - RAG: Need to query knowledge base (provide query)
    - USER: Need user input to fill placeholders or solve security questions (provide question)
    
    IMPORTANT RULES:
    - If you choose anything except READY, you MUST provide a clear explanation of the issue
    - Issues should only be bugs, syntax errors, placeholders, or inconsistencies with the implementation plan
    - DO NOT raise issues about items that were already answered in previous user responses
    - For RAG or USER decisions, you MUST provide the exact query needed
    - For FIX, your explanation must clearly state what's wrong
    - Be specific and concise in your explanation
    - Before asking a user question, check if it was already answered in previous responses
    
    Example Response 1:
    {{
        "decision": "READY",
        "explanation": "All code matches implementation plan perfectly"
    }}
    
    Example Response 2:
    {{
        "decision": "USER",
        "explanation": "Missing IP address for security group configuration",
        "next_query": "Please provide the IP address that should be allowed SSH access"
    }}
    """

    class ReviewDecision(BaseModel):
        """Model for code review decisions"""
        decision: Literal["READY", "FIX", "RAG", "USER"] = Field(
            description="The decision about the code changes"
        )
        explanation: str = Field(
            description="Explanation for the decision"
        )
        next_query: Optional[str] = Field(
            description="The next query to execute (required for RAG and USER decisions)",
            default=None
        )

    try:
        result = llm.with_structured_output(ReviewDecision).invoke(prompt)
        
        # Check if this question was already asked
        if result.decision == "USER" and result.next_query:
            for past_response in past_responses:
                if (result.next_query.lower().strip() == past_response['prompt'].lower().strip() or
                    result.explanation.lower().strip() in past_response['prompt'].lower().strip()):
                    logger.info(f"Question already answered: {result.next_query}")
                    # Re-run review with the answer
                    state["code_query_decision"] = "execute"
                    return state
        
        # Log the review decision and explanation
        logger.info(f"Review Decision: {result.decision}")
        logger.info(f"Explanation: {result.explanation}")
        if result.next_query:
            logger.info(f"Next Query: {result.next_query}")
        
        # Store review result
        if 'reviews' not in state["memory"]:
            state["memory"]['reviews'] = []
        state["memory"]['reviews'].append({
            'decision': result.decision,
            'explanation': result.explanation,
            'next_query': result.next_query,
            'timestamp': datetime.now().isoformat()
        })
        
        # Set next node based on decision
        state["code_query_decision"] = result.decision.lower()
        
        if result.decision == "FIX":
            # Store the explanation for use in the next iteration
            state["memory"]['current_issue'] = result.explanation
        elif result.decision == "RAG":
            if not result.next_query:
                logger.error("RAG decision requires next_query")
                state["code_query_decision"] = "end"
            else:
                state["memory"]['current_rag_query'] = result.next_query
        elif result.decision == "USER":
            if not result.next_query:
                logger.error("USER decision requires next_query")
                state["code_query_decision"] = "end"
            else:
                state["memory"]['current_user_prompt'] = result.next_query
            
        state["iteration_count"] += 1
        
        if state["iteration_count"] >= state["max_iterations"]:
            logger.warning("Max iterations reached")
            state["code_query_decision"] = "end"
            
    except Exception as e:
        logger.error(f"Error during code review: {str(e)}")
        state["code_query_decision"] = "end"
    
    return state

def query_rag(state: ExecutionState) -> ExecutionState:
    """Query RAG for missing info."""
    append_state_update(state["repo_path"], "execution", "query_rag", "Querying knowledge base for context")
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
    append_state_update(state["repo_path"], "execution", "handle_user_input", "Requesting user input for decisions")
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

        # Store user input with context
        if 'user_inputs' not in state["memory"]:
            state["memory"]['user_inputs'] = []
        state["memory"]['user_inputs'].append({
            'prompt': prompt,
            'response': user_response,
            'context': state["current_files"],  # Store current file context
            'timestamp': datetime.now().isoformat()
        })
        
        # Reset iteration count to ensure changes are applied
        state["iteration_count"] = 1
        state["code_query_decision"] = "execute"
        
    except Exception as e:
        logger.error(f"Error handling user input: {str(e)}", exc_info=True)
        state["code_query_decision"] = "end"
    
    return state

def create_execution_graph():
    """Creates the execution workflow graph."""
    workflow = StateGraph(ExecutionState)
    
    # Add nodes
    workflow.add_node("execute", code_executor)
    workflow.add_node("review", code_reviewer)
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

def start_execution_agent(
    planning_state: dict,
    forge=None,
    rag_utils: Optional[RAGUtils] = None,
    max_iterations: int = 5
) -> dict:
    """Start the execution agent."""
    print("\n=== Starting Execution Agent ===")
    
    initial_state = ExecutionState(
        messages=[SystemMessage(content="Starting execution process")],
        memory={
            'forge': forge,
            'rag_utils': rag_utils
        },
        implementation_plan=planning_state["implementation_plan"],
        repo_path=planning_state["repo_path"],
        files_to_edit=planning_state["files_to_edit"],
        code_execution_output="",
        iteration_count=1,
        max_iterations=max_iterations,
        validation_ready=False,
        current_files={},
        code_query_decision="execute",
        rag_utils=rag_utils
    )
    
    workflow = create_execution_graph()
    final_state = workflow.invoke(initial_state)
    
    return final_state
