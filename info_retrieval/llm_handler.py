import os
import asyncio
import logging
from typing import List

from langchain.chat_models import ChatOpenAI
from langchain.prompts import PromptTemplate

from models import Subtask, Plan

logger = logging.getLogger(__name__)

class LLMHandler:
    def __init__(self):
        openai_api_key = os.getenv("OPENAI_API_KEY")
        self.plan_llm = self.llm.with_structured_output(Plan, method="json_mode")
        if not openai_api_key:
            raise EnvironmentError("OPENAI_API_KEY is missing.")
        self.llm = ChatOpenAI(
            model="gpt-4",
            temperature=0.7,
            openai_api_key=openai_api_key
        )

    async def generate_plan(self, user_query: str) -> Plan:
        """Generates a plan consisting of multiple subtasks."""
        prompt = PromptTemplate(
            input_variables=["user_query"],
            template="""
            You are an expert software engineer.

            Based on the user's request, generate a plan that consists of multiple steps.

            Each step should have:
            - A filename.
            - In the in-depth implementation outline, ensure to mention that it must contain a __main__ function that can be used to simply test that file. Furthermore, it should specify the name of the file that needs to be created for this module.
            - An expected output description.

            The last subtask should be a file that puts everything together.

            Provide the plan as a JSON object with a key "subtasks" which is a list of subtasks. Each subtask should have:
            - "filename"
            - "implementation_outline"
            - "expected_output_description"

            Example:

            {
                "subtasks": [
                    {
                        "filename": "module1.py",
                        "implementation_outline": "...",
                        "expected_output_description": "..."
                    },
                    ...
                ]
            }

            User's request: {user_query}
            """
                    )


        print("Invoking test functions LLM")
        response = self.plan_llm.invoke(
             prompt.format(
                    user_query=user_query
                )
            )

        return response.commands

async def validate_output(self, output: str, expected_description: str) -> bool:
    """Validates the output against the expected description using the LLM."""
    prompt = f"""
        You are an assistant that validates whether the output matches the expected description.

        Output: {output}

        Expected Description: {expected_description}

        Does the output match the expected description? Answer "Yes" or "No". Nothing else."""

    response = await self.llm.agenerate([prompt])
    return response.generations[0][0].text.strip() == "Yes"

