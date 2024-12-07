import os
import logging
from pathlib import Path
from typing import List, Dict, Optional
import google.generativeai as genai
from pydantic import BaseModel, Field
from langchain_openai import ChatOpenAI

# Initialize LLMs
openai_llm = ChatOpenAI(model="gpt-4o", temperature=0)
genai.configure(api_key=os.getenv("GOOGLE_API_KEY"))
gemini_llm = genai.GenerativeModel(model_name="gemini-1.5-pro")

class QueryResponseSchema(BaseModel):
    """Schema for RAG query responses"""
    answer: str = Field(description="The answer to the query based on the provided context")
    relevant_files: List[str] = Field(description="List of files that were most relevant to answering the query")
    confidence: float = Field(description="Confidence score between 0 and 1")

class RAGUtils:
    def __init__(self, repo_path: str):
        self.repo_path = Path(repo_path)
        
    def _read_file(self, file_path: str) -> str:
        """Read a file and return its content"""
        try:
            with open(file_path, 'r') as f:
                return f.read()
        except Exception as e:
            logging.error(f"Error reading file {file_path}: {e}")
            return ""

    def _get_file_contents(self, file_paths: List[str]) -> Dict[str, str]:
        """Get contents of multiple files with their paths as keys"""
        contents = {}
        for file_path in file_paths:
            full_path = os.path.join(self.repo_path, file_path)
            if os.path.exists(full_path):
                content = self._read_file(full_path)
                if content:
                    contents[file_path] = content
        return contents

    def query_codebase(self, query: str, file_paths: List[str]) -> QueryResponseSchema:
        """Query the codebase using Gemini"""
        file_contents = self._get_file_contents(file_paths)
        
        if not file_contents:
            return QueryResponseSchema(
                answer="No relevant files found or could not read files.",
                relevant_files=[],
                confidence=0.0
            )

        # Prepare context for Gemini
        context = "File contents for analysis:\n\n"
        for path, content in file_contents.items():
            context += f"File: {path}\n```\n{content}\n```\n\n"

        prompt = f"""As an Infrastructure as Code expert, analyze these files and answer the following query.
        Consider all provided files and their relationships.
        
        Query: {query}
        
        {context}
        
        Provide a detailed answer that:
        1. Directly addresses the query
        2. References specific files and code sections
        3. Explains any relevant relationships between files
        4. Highlights important configurations or patterns
        5. Notes any potential issues or considerations
        
        Format your response to be clear and structured.
        """

        try:
            response = gemini_llm.generate_content(prompt)
            
            # Use OpenAI to structure the response
            structure_prompt = f"""
            Convert this raw response into a structured format:
            
            Raw Response:
            {response.text}
            
            Extract:
            1. The main answer
            2. List of files that were most relevant
            3. A confidence score (0-1) based on how well the answer addresses the query
            """
            
            structured = openai_llm.with_structured_output(QueryResponseSchema).invoke(structure_prompt)
            return structured
            
        except Exception as e:
            logging.error(f"Error querying codebase: {e}")
            return QueryResponseSchema(
                answer=f"Error querying codebase: {str(e)}",
                relevant_files=list(file_contents.keys()),
                confidence=0.0
            )

    def get_context_for_query(self, query: str, file_paths: List[str]) -> str:
        """Get relevant context from files for a specific query"""
        response = self.query_codebase(query, file_paths)
        
        if not response.relevant_files:
            return "No relevant context found."
            
        context = f"Query: {query}\n\n"
        context += f"Answer: {response.answer}\n\n"
        context += "Relevant files:\n"
        for file in response.relevant_files:
            context += f"- {file}\n"
        context += f"\nConfidence: {response.confidence}\n"
            
        return context 