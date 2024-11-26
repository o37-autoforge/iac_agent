from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field
import os
import json
import google.generativeai as genai

# Initialize LLMs
openai_llm = ChatOpenAI(model="gpt-4o", temperature=0, openai_api_key=os.getenv("OPENAI_API_KEY"))
genai_api_key = os.getenv("GENAI_API_KEY")
genai.configure(api_key=genai_api_key)
gemini_llm = genai.GenerativeModel(model_name="gemini-1.5-pro")

# Define Pydantic Schemas
class RelevantFilesSchema(BaseModel):
    """
    Schema for returning a list of relevant file paths.
    """
    relevant_files: list[str] = Field(
        description="List of relative file paths that are relevant to the query."
    )

def choose_relevant_IaC_files(file_descriptions: str, query: str) -> RelevantFilesSchema:
    """
    Analyzes IaC file descriptions and determines relevant files for a specific query. 

    Args:
        file_descriptions (str): Combined set of all file descriptions and relative paths as a string.
        query (str): The query specifying the task or issue to resolve.

    Returns:
        RelevantFilesSchema: Structured output containing a list of relevant file paths.
    """
    # Bind the schema to the model
    model_with_structure = openai_llm.with_structured_output(RelevantFilesSchema)

    prompt = f"""
    You are an expert in Infrastructure as Code (IaC). Analyze the following file descriptions and their relative paths 
    to identify the files that might need to be referred to or edited to implement or address the query:

    Query: {query}

    File Descriptions:
    {file_descriptions}
    """

    structured_output = model_with_structure.invoke(prompt)
    return structured_output

def choose_relevant_aws_files(aws_file_tree: str, query: str) -> RelevantFilesSchema:
    """
    Analyzes AWS data and determines relevant files for information retrieval.

    Args:
        aws_file_tree (str): File tree representation of AWS data and relative paths.
        query (str): Query specifying the data or information to retrieve.

    Returns:
        RelevantFilesSchema: Structured output containing a subset of AWS file paths relevant to the query.
    """
    # Bind the schema to the model
    model_with_structure = openai_llm.with_structured_output(RelevantFilesSchema)

    prompt = f"""
    You are an expert in AWS infrastructure and data analysis. Given the following file tree of AWS data, 
    analyze and determine which files might need to be accessed or edited to answer the query:

    Query: {query}

    AWS File Tree:
    {aws_file_tree}
    """

    structured_output = model_with_structure.invoke(prompt)
    return structured_output

# 3. Information Retrieval Agent
class InformationRetrievalSchema(BaseModel):
    """
    Schema for answering queries from file content.
    """
    answer: str = Field(description="The answer to the user's query based on the provided file content.")

def retrieve_information(file_content: str, query: str) -> InformationRetrievalSchema:
    """
    Answers a query using the full content of a specific file. 

    Args:
        file_content (str): Full content of the file as a string.
        query (str): Query specifying the information to retrieve.

    Returns:
        InformationRetrievalSchema: Structured response to the query.
    """
    # Bind the schema to Gemini
    prompt = f"""
    You are an expert in analyzing code and data. Answer the following query using the provided file content. Be specific. 

    Query: {query}

    File Content:
    {file_content}

    Give all relevant information. Count carefully if needed.
    """

    response = gemini_llm.generate_content(prompt)
    answer = response.strip()

    # Parse into structured schema
    structured_output = InformationRetrievalSchema(answer=answer)
    return structured_output
