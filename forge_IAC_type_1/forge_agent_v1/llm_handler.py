import os
import json
import logging
from langchain_openai import ChatOpenAI
from langchain.prompts import PromptTemplate
from .models import forgeQuestions, UserQuestions, TaskDecomposition, TestFunctions, UserQuestion, forgeQuery, errorQuery   
from typing import List, Dict
from pathlib import Path

logger = logging.getLogger(__name__)

class LLMHandler:
    def __init__(self, repo_path: str):
        self.llm = ChatOpenAI(
            model="gpt-4o",
            temperature=0.5,
            openai_api_key=os.getenv("OPENAI_API_KEY")
        )
        self.repo_path = Path(repo_path)
        # Configure structured output using JSON mode
        self.structured_llm = self.llm.with_structured_output(forgeQuestions, method="json_mode")
        self.query_llm = self.llm.with_structured_output(forgeQuery, method="json_mode")
        self.user_questions_llm = self.llm.with_structured_output(UserQuestions, method="json_mode")
        self.decomposition_llm = self.llm.with_structured_output(TaskDecomposition, method="json_mode")
        self.test_functions_llm = self.llm.with_structured_output(TestFunctions, method="json_mode")
        self.error_query_llm = self.llm.with_structured_output(errorQuery, method="json_mode")
        self.response_llm = ChatOpenAI(
            model="gpt-4o",
            temperature=0.3,
            openai_api_key=os.getenv("OPENAI_API_KEY")
        )

    def get_repo_content(self) -> str:
        """
        Reads and returns the content of relevant files from the repository.
        Focuses on Terraform files and related infrastructure code.
        """
        relevant_files = []
        try:
            # Walk through the repository
            for root, _, files in os.walk(self.repo_path):
                for file in files:
                    # Check for relevant file extensions
                    if file.endswith(('.tf', '.tfvars', '.hcl')):
                        file_path = Path(root) / file
                        try:
                            with open(file_path, 'r', encoding='utf-8') as f:
                                content = f.read()
                                relevant_files.append(f"=== {file} ===\n{content}\n")
                        except Exception as e:
                            logger.error(f"Error reading file {file_path}: {e}")

            if not relevant_files:
                return "No existing Terraform files found in the repository."
            
            return "\n".join(relevant_files)

        except Exception as e:
            logger.error(f"Error reading repository content: {e}")
            return "Error reading repository content."

    def generate_forge_query(self, user_query: str, user_responses: List[dict]) -> str:

        formatted_user_responses = [
            {
                "question": resp['question']['question'],
                "response": resp['response']
            } for resp in user_responses
        ]
        # Compose a prompt for the LLM based on the detected question and codebase context
        prompt = f"""

        You are an expert Terraform developer. Your job is to generate a well written query for forge, an AI coding agent, for implementing the user's request
        and ensuring the codebase is updated correctly. Base your query for forge on the user's request and the answers from the user about specific details below.

        Question:
        "{user_query}"
        
        Specifications from the user:
        "{json.dumps(formatted_user_responses, indent=2)}"

        Your response should be a json with a "task" key.
        """

        try:
            response = self.query_llm.invoke(prompt)
            logger.debug(f"Generated LLM response: {response}")
            return response.task.strip()
        except Exception as e:
            logger.error(f"Failed to generate response using LLM: {e}")
            raise

    def generate_error_query(self, starting_query: str, error_message: str, command: str) -> str:
 
        # Compose a prompt for the LLM based on the detected question and codebase context
        prompt = f"""

        You are an expert Terraform developer. Your job is to generate a well written query to help forge, an AI coding agent, solve the error it received after running
        the {command} via CLI. The original query was "{starting_query}". The error message is "{error_message}".

        Your response should be a json with a "query" key.
        """

        try:
            response = self.error_query_llm.invoke(prompt)
            logger.debug(f"Generated LLM response: {response}")
            return response.query.strip()
        except Exception as e:
            logger.error(f"Failed to generate response using LLM: {e}")
            raise

    def generate_user_questions(self, user_query: str) -> List[UserQuestion]:
        """
        Generates questions to ask the user based on their query, and forge's responses about the codebase.
        """
        try:
            prompt = PromptTemplate(
                template="""
                You are an expert Terraform developer. Your job is to generate specific questions that need to be answered by the user before implementing their infrastructure request.

                This is the users query: {user_query}

                Based on the specific query, generate questions that will help clarify:
                1. Resource configuration requirements
                2. Security and compliance needs
                3. Naming conventions and tags
                4. Integration requirements
                5. Performance and scaling preferences

                **Format for Each Question:**
                - `question`: The specific question to ask.
                - `context`: Important context that helps the user understand why this question matters.
                - `default`: A reasonable default answer as a string (use JSON-formatted strings for complex values).

                Generate at least 3 questions, focusing on the most critical configuration decisions.
                """,
                input_variables=["user_query"]
            )

            response = self.user_questions_llm.invoke(
                prompt.format(
                    user_query=user_query,
                )
            )

            print(response)

            if not response.questions:
                logger.warning("No questions were generated!")
                # Provide some default questions as fallback
                return [
                    UserQuestion(
                        question="What should be the name of the resource?",
                        context="Resource naming helps with identification and management.",
                        default="terraform-resource"
                    ),
                    UserQuestion(
                        question="What tags should be applied to the resource?",
                        context="Tags help with resource organization and cost tracking.",
                        default='{"Environment": "dev", "ManagedBy": "terraform"}'
                    )
                ]

            return response.questions

        except Exception as e:
            logger.error(f"Failed to generate user questions: {str(e)}")
            raise
