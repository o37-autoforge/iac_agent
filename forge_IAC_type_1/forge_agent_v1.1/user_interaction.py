# forge_agent/user_interaction.py

import logging
import sys
from typing import List

from .models import UserResponse, UserQuestion

logger = logging.getLogger(__name__)
def disable_logging():
    original_log_handlers = logging.getLogger().handlers[:]
    for handler in original_log_handlers:
        logging.getLogger().removeHandler(handler)

disable_logging()

class UserInteraction:
    def __init__(self, llm_handler, subprocess_handler, logs_dir, git_handler):
        self.llm_handler = llm_handler
        self.subprocess_handler = subprocess_handler
        self.logs_dir = logs_dir
        self.git_handler = git_handler

    async def generate_questions_for_user(self, user_query: str) -> List[UserQuestion]:

        # Generate user questions based on query
        user_questions = self.llm_handler.generate_user_questions(user_query)
        return user_questions

    async def ask_user_questions(self, questions: List[UserQuestion]) -> List[UserResponse]:
        """
        Asks the user each question and collects their responses.

        :param questions: List of UserQuestion objects to ask
        :return: List of UserResponse objects
        """
        responses = []
        print("\nPlease answer the following questions to help me understand your requirements:")

        for i, question in enumerate(questions, 1):
            print(f"\nQuestion {i}: {question.question}")
            print(f"Context: {question.context}")
            print(f"Default answer: {question.default}")

            while True:
                response = input("\nYour answer (press Enter to use default): ").strip()
                if not response:
                    response = question.default
                    print(f"Using default answer: {response}")

                confirm = input("Confirm this answer? (y/n): ").lower()
                if confirm == 'y':
                    break
                print("Let's try again...")

            responses.append(UserResponse(question=question, response=response))

        return responses

    async def handle_user_interaction(self):
        print("Type your IaC query below. Type 'exit' to quit.\n")

        user_query = input("Enter your IaC query: ").strip()
        if not user_query:
            logger.warning("Empty query provided.")
            print("Please enter a valid query.\n")
            return False
        if user_query.lower() in ['exit', 'quit']:
            logger.info("Exiting forge Agent as per user request.")
            await self.subprocess_handler.close_forge()
            print("Goodbye!")
            return False

        # Process initial query and get user responses
        questions = await self.generate_questions_for_user(user_query)

        self.user_responses = await self.ask_user_questions(questions)

        # Get well-written query for forge
        starting_query = self.llm_handler.generate_forge_query(
            user_query, [resp.model_dump() for resp in self.user_responses]
        )

        self.starting_query = starting_query
        return user_query, starting_query

    async def prompt_user_for_input(self, prompt_message: str) -> str:
        print(f"\nSubprocess is requesting input: {prompt_message}")
        user_input = input("Your input: ").strip()
        return user_input

    def save_user_responses(self, timestamp: str, sanitized_query: str, responses: List[UserResponse]):
        try:
            responses_filepath = self.logs_dir / f"{timestamp}_{sanitized_query}_responses.txt"

            with open(responses_filepath, 'w', encoding='utf-8') as response_file:
                response_file.write(f"User Responses - {timestamp}\n\n")

                for i, response in enumerate(responses, 1):
                    response_file.write(f"Question {i}:\n")
                    response_file.write(f"Q: {response.question.question}\n")
                    response_file.write(f"Context: {response.question.context}\n")
                    response_file.write(f"Default: {response.question.default}\n")
                    response_file.write(f"User Response: {response.response}\n\n")

            logger.info(f"User responses saved to {responses_filepath}")

        except Exception as e:
            logger.error(f"Failed to save user responses: {str(e)}")
