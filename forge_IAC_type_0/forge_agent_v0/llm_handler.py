# llm_handler.py

import os
import json
import logging
from langchain_openai import ChatOpenAI
from langchain.prompts import PromptTemplate
from .models import forgeQuestions, UserQuestions, TaskDecomposition, TestFunctions, UserQuestion, forgeQuery
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
        self.response_llm = ChatOpenAI(
            model="gpt-4o",
            temperature=0.3,
            openai_api_key=os.getenv("OPENAI_API_KEY")
        )

    def get_repo_content(self) -> str:
        """
        Reads and returns the content of relevant files from the repository.
        Focuses on relevant code files.
        """
        relevant_files = []
        try:
            # Walk through the repository
            for root, _, files in os.walk(self.repo_path):
                for file in files:
                    # Check for relevant file extensions
                    if file.endswith(('.py', '.tf', '.tfvars', '.hcl', '.js', '.ts', '.java', '.go')):
                        file_path = Path(root) / file
                        try:
                            with open(file_path, 'r', encoding='utf-8') as f:
                                content = f.read()
                                relevant_files.append(f"=== {file} ===\n{content}\n")
                        except Exception as e:
                            logger.error(f"Error reading file {file_path}: {e}")

            if not relevant_files:
                return "No relevant files found in the repository."
            
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
        # Compose a prompt for the LLM based on the user's query and their answers
        prompt = f"""
        You are an expert developer acting as a copilot. Your job is to generate a detailed task description for forge, an AI coding agent, to implement the user's request and update the codebase accordingly. Base your task description on the user's request and their answers to specific questions.

        User's Request:
        "{user_query}"

        User's Answers:
        {json.dumps(formatted_user_responses, indent=2)}

        Provide a clear and actionable task description for forge to execute.

        **Output Format:**

        Return a JSON object with a single key "task", where "task" is the task description.

        **Example Output:**

        ```json
        {{
        "task": "Implement a new S3 bucket named 'my-unique-bucket-name' with private access, in the 'us-east-1' region."
        }}
        ```
        """

        try:
            print("Invoking query LLM")
            response = self.query_llm.invoke(prompt)
            print(f"LLM response: {response}")
            logger.debug(f"Generated LLM response: {response}")
            return response.task.strip()
        except Exception as e:
            logger.error(f"Failed to generate response using LLM: {e}")
            raise


    def generate_user_questions(self, user_query: str) -> List[UserQuestion]:
        """
        Generates questions to ask the user based on their query.
        """
        try:
            prompt = PromptTemplate(
                template="""
                You are an expert developer acting as a copilot. Your job is to generate specific questions that need to be answered by the user before implementing their request.

                User's Query: {user_query}

                Based on the specific query, generate questions that will help clarify any ambiguities or gather necessary details to proceed.

                **Output Format:**

                Return a JSON object with a single key `"questions"`, which is a list of question objects. Each question object should have the following keys:
                - `question`: The specific question to ask.
                - `context`: Important context that helps the user understand why this question matters.
                - `default`: A reasonable default answer as a string (use JSON-formatted strings for complex values).

                Generate up to 3 questions, focusing on the most critical information needed.

                **Example Output:**

                ```json
                {{
                "questions": [
                    {{
                    "question": "What is the name of the resource?",
                    "context": "Resource naming helps with identification and management.",
                    "default": "my-resource"
                    }},
                    {{
                    "question": "Do you have specific tags you want to apply?",
                    "context": "Tags help with resource organization and cost tracking.",
                    "default": "{{\\"Environment\\": \\"dev\\", \\"Owner\\": \\"team-name\\"}}"
                    }}
                ]
                }}
                ```
                """,
                input_variables=["user_query"]
            )



            response = self.user_questions_llm.invoke(
                prompt.format(
                    user_query=user_query,
                )
            )

            logger.debug(f"Generated user questions: {response}")

            if not response.questions:
                logger.warning("No questions were generated!")
                # Provide some default questions as fallback
                return [
                    UserQuestion(
                        question="Can you provide more details about the functionality you want to implement?",
                        context="Detailed requirements help in accurate implementation.",
                        default="No additional details."
                    )
                ]

            return response.questions

        except Exception as e:
            logger.error(f"Failed to generate user questions: {str(e)}")
            raise
