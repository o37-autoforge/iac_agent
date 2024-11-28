from typing import TypedDict, Annotated, List, Dict
import operator
from langchain_core.messages import BaseMessage
from langgraph.prebuilt import ToolExecutor
from langgraph.graph import StateGraph, END
from langchain.tools.render import format_tool_to_openai_function

# Import custom tools
from tools import get_tool_functions, ToolExecution

# Define the agent state
class AgentState(TypedDict):
    messages: Annotated[List[BaseMessage], operator.add]
    query: str
    repo_path: str
    combined_file_path: str
    aws_identity: str
    file_descriptions: Dict
    file_tree: str
    codebase_overview: str
    edit_code_decision: str
    files_to_edit: List[str]
    implementation_plan: str
    tool_response: str
    decision: str  # Whether to use a tool, generate a new query, or finish
    tool_query: str
    old_files_content: str
    new_files_content: str
    logs: str

# Initialize tool executor with dynamically loaded tools
tools = get_tool_functions()  # Fetch all tools
tool_executor = ToolExecutor(tools)
formatted_tools = [format_tool_to_openai_function(tool) for tool in tools]

# Define the code editing node
def code_editing_agent(state: AgentState) -> AgentState:
    query = state["implementation_plan"]
    #Send query to "forge" agent. Get old files, new files, and logs in return.

    

    # Define the linter node (decision-making on whether to use tools)
def linter_node(state: AgentState) -> AgentState:
    new_files = state["new_files_content"]
    logs = state["logs"]
    
    # Decision logic
    if "placeholder" in new_files or "syntax error" in logs:
        decision = "use_tool"  # Tool required for placeholders or errors
    elif "successfully implemented" in logs:
        decision = "end"  # Task successfully completed
    else:
        decision = "generate_query"  # No tool needed, generate new query
    
    # Update state with decision
    state.update({"decision": decision})
    return state

# Generate query for the chosen tool (if needed)
def generate_tool_query(state: AgentState) -> AgentState:
    decision = state["decision"]
    if decision == "use_tool":
        context = (
            f"Based on the code analysis, logs, and the state of the project, generate a query "
            f"to retrieve relevant information using the '{decision}' tool."
        )
        # Generate the query dynamically using LLM
        tool_query = ToolExecution.generate_query(state["messages"], context)
        state.update({"tool_query": tool_query})
    return state

# Execute the chosen tool
def execute_tool(state: AgentState) -> AgentState:
    decision = state["decision"]
    query = state["tool_query"]
    response = tool_executor.invoke({"tool": decision, "query": query})
    state.update({"tool_response": response})
    return state

# Define a function to generate a new query for code editing
def generate_query(state: AgentState) -> AgentState:
    # Simulate query generation for the code editing agent
    updated_query = "Updated query based on analysis"
    state.update({"query": updated_query})
    return state

def create_code_editing_agent():
    # Construct the state graph
    graph = StateGraph(AgentState)

    # Add nodes
    graph.add_node("code_editing", code_editing_agent)
    graph.add_node("linter", linter_node)
    graph.add_node("generate_tool_query", generate_tool_query)
    graph.add_node("execute_tool", execute_tool)
    graph.add_node("generate_query", generate_query)

    # Add edges
    graph.set_entry_point("code_editing")  # Start at code editing node
    graph.add_edge("code_editing", "linter")  # Always go to linter after code editing
    graph.add_conditional_edges(
        "linter",
        lambda state: state["decision"],  # Decision drives which node is called next
        {
            "use_tool": "generate_tool_query",
            "generate_query": "generate_query",
            "end": END,
        }
    )
    graph.add_edge("generate_tool_query", "execute_tool")  # Generate query before executing tool
    graph.add_edge("execute_tool", "code_editing")  # Return to code editing after tool execution
    graph.add_edge("generate_query", "code_editing")  # Return to code editing after generating new query

    # Compile the graph into a runnable application
    app = graph.compile()

# Example usage
inputs = {
    "messages": [],
    "query": "Initial coding task",
    "repo_path": "/path/to/repo",
    "combined_file_path": "/path/to/combined/file",
    "aws_identity": "aws_user_id",
    "file_descriptions": {"file1": "desc1", "file2": "desc2"},
    "file_tree": "root > folder1 > file1",
    "codebase_overview": "summary of codebase",
    "edit_code_decision": "",
    "files_to_edit": ["file1.py", "file2.py"],
    "implementation_plan": "implement feature X",
    "tool_response": "",
    "decision": "",
    "tool_query": "",
    "old_files_content": "",
    "new_files_content": "",
    "logs": "",
}

output = app.invoke(inputs)
print(output)
