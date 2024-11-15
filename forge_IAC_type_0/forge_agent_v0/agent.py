# forge_agent/agent.py

import asyncio
import json
import os
import logging
import traceback
import sys
from pathlib import Path
from datetime import datetime
from typing import List, Dict
from dotenv import load_dotenv
from .githubHandler import GitHandler
from .models import (
    forgeQuestions,
    UserQuestion,
    UserQuestions,
    UserResponse,
    TaskDecomposition,
    TestFunctions
)
import re
from .utils import strip_ansi_codes, remove_consecutive_duplicates, sanitize_filename, clean_response, clean_forge_response
from .llm_handler import LLMHandler
from .subprocess_handler import SubprocessHandler
import git

logger = logging.getLogger(__name__)

class forgeAgent:
    def __init__(self):
        # Load environment variables
        load_dotenv()
        OPEN_AI_KEY = os.getenv("OPENAI_API_KEY")
        if not OPEN_AI_KEY:
            logger.error("OPENAI_API_KEY is not set in environment variables.")
            raise EnvironmentError("OPENAI_API_KEY is missing.")

        # Initialize GitHandler and clone the repository
        self.git_handler = GitHandler()
        self.repo = self.git_handler.clone_repository()
        self.repo_path = self.git_handler.repo_path
        try:
            self.repo.git.checkout(self.git_handler.branch_name)
            logger.info(f"Checked out to existing branch '{self.git_handler.branch_name}'")
        except git.exc.GitCommandError:
            # Branch doesn't exist; create it
            self.repo.git.checkout('-b', self.git_handler.branch_name)
            logger.info(f"Created and checked out to new branch '{self.git_handler.branch_name}'")

        # Initialize LLM Handler
        self.llm_handler = LLMHandler(repo_path=str(self.repo_path))

        # Initialize Subprocess Handler and start forge
        self.subprocess_handler = SubprocessHandler(self.repo_path)
        try:
            self.subprocess_handler.start_forge(OPEN_AI_KEY)
        except Exception as e:
            logger.error(f"Failed to start forge: {str(e)}")
            raise

        # Prepare the logs directory
        self.logs_dir = self.repo_path / "forge_logs"
        self.logs_dir.mkdir(exist_ok=True)
        logger.info(f"Logs will be saved to directory: {self.logs_dir}")

        self.user_responses: List[UserResponse] = []  # To store user responses
        self.forge_responses: Dict[str, str] = {}     # To store forge responses

    async def set_forge_mode(self, mode: str) -> bool:
        """
        Changes forge's mode between 'ask' and 'code'
        
        :param mode: Either 'ask' or 'code'
        :return: Boolean indicating success
        """
        try:
            if mode not in ['ask', 'code']:
                raise ValueError(f"Invalid mode: {mode}. Must be 'ask' or 'code'")
            
            logger.info(f"Changing forge mode to: {mode}")
            self.subprocess_handler.child.sendline(f"/chat-mode {mode}")
            
            # Expect the new prompt based on mode
            starter = "ask" if mode == "ask" else ""
            expected_prompt = f"{starter}>"
            self.subprocess_handler.child.expect(expected_prompt, timeout=60)
            
            logger.info(f"Successfully changed to {mode} mode")
            return True
            
        except Exception as e:
            logger.error(f"Failed to set forge mode to {mode}: {e}")
            return False

    async def ask_forge_question(self, question: str) -> str:
        """
        Asks forge a question in 'ask' mode
        
        :param question: The question to ask
        :return: forge's response
        """
        try:
            # Ensure we're in ask mode
            if not await self.set_forge_mode('ask'):
                raise Exception("Failed to set ask mode")
            
            response = await self.subprocess_handler.send_command(question, current_mode='ask')
            cleaned_response = strip_ansi_codes(response)
            cleaned_response = remove_consecutive_duplicates(cleaned_response)
            logger.debug(f"forge response: {cleaned_response}")
            return cleaned_response.strip()
            
        except Exception as e:
            logger.error(f"Failed to ask forge question: {e}")
            raise

    async def send_code_command(self, command: str) -> str:
        """
        Sends a code-related command to forge in 'code' mode
        
        :param command: The code command to send
        :return: forge's response
        """
        try:
            # Ensure we're in code mode
            if not await self.set_forge_mode('code'):
                raise Exception("Failed to set code mode")
            
            response = await self.subprocess_handler.send_command(command, current_mode='code')
            print(response)
            await self.subprocess_handler.send_command("/commit", current_mode='code')
            cleaned_response = strip_ansi_codes(response)
            cleaned_response = remove_consecutive_duplicates(cleaned_response)
            logger.debug(f"forge response: {cleaned_response}")
            return cleaned_response.strip()
            
        except Exception as e:
            logger.error(f"Failed to send code command: {e}")
            raise

    async def generate_questions_for_user(self, user_query: str) -> dict:
        # Generate user questions based on query
        user_questions = self.llm_handler.generate_user_questions(user_query)
        return user_questions
    
    async def ask_user_questions(self, questions: UserQuestions) -> List[UserResponse]:
        """
        Asks the user each question and collects their responses.
        
        :param questions: List of UserQuestion objects to ask
        :return: List of UserResponse objects
        """
        responses = []
        print("\nPlease answer the following questions about your requirements:")
        
        for i, question in enumerate(questions, 1):
            print(f"\nQuestion {i}:")
            print(f"{question.question}")
            print(f"\nContext: {question.context}")
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

    async def execute_subtask(self, task: str) -> bool:
        """
        Executes a single subtask using forge in code mode
        """
        logger.info(f"Executing subtask {task}")                    
        # Send the actual query to forge
        response = await self.send_code_command(task)
            
        # Check if there were any errors in the response
        if "error" in response.lower() or "failed" in response.lower():
            logger.error(f"forge reported an error for subtask {task}: {response}")
            return False
                
        logger.info(f"Successfully completed subtask {task}")
        return True

    async def handle_user_interaction(self):
        """Handles the interaction with the user and task decomposition"""
        print("Type your query below. Type 'exit' to quit.\n")
        
        while True:
            user_query = input("Enter your query: ").strip()
            if not user_query:
                logger.warning("Empty query provided.")
                print("Please enter a valid query.\n")
                continue
            if user_query.lower() in ['exit', 'quit']:
                logger.info("Exiting forge Agent as per user request.")
                await self.close_forge()
                print("Goodbye!")
                break

            # Process initial query and get user responses
            results = await self.generate_questions_for_user(user_query)                
            self.user_responses = await self.ask_user_questions(results)
                    
            # Get well-written query for forge. 
            forge_query = self.llm_handler.generate_forge_query(user_query, [resp.model_dump() for resp in self.user_responses])

            print(f"forge_query: {forge_query}")
            # Send query to forge
            await self.execute_subtask(forge_query)

            # Ask the user if they want to continue, review, or create a PR
            while True:
                next_action = input("\nDo you want to (c)ontinue with another query, (r)eview changes, or (p)ush changes to create a PR? (c/r/p): ").lower()
                if next_action == 'c':
                    # Continue with another query
                    break  # Break inner loop to input a new query
                elif next_action == 'r':
                    # Review changes (e.g., show git diff)
                    print("\nReviewing changes:\n")
                    diff = self.repo.git.diff()
                    print(diff)
                elif next_action == 'p':
                    # Push changes and create PR
                    await self.push_changes_and_create_pr(user_query)
                    await self.close_forge()
                    print("Changes have been pushed and a PR has been created.")
                    return self.repo
                else:
                    print("Invalid option. Please enter 'c', 'r', or 'p'.")

    async def push_changes_and_create_pr(self, commit_message: str):
        """
        Adds, commits, and pushes changes to the repository, then creates a PR.
        """
        self.repo.git.add(A=True)
        origin = self.repo.remote(name='origin')
        origin.push(self.git_handler.branch_name)
        logger.info(f"Pushed changes to branch '{self.git_handler.branch_name}'")

        # Create a pull request
        pr = self.git_handler.create_pull_request(
            title=commit_message,
            body="This PR includes code changes made by the forge agent."
        )
        logger.info(f"Pull request created: {pr.html_url}")

    async def close_forge(self):
        """
        Closes the forge process gracefully.
        """
        try:
            self.subprocess_handler.close_forge()
        except Exception as e:
            logger.error(f"Error while closing forge: {str(e)}")
            print(f"An error occurred while closing forge: {str(e)}")

    def run_subprocess(self, command: str):
        """
        Runs the subprocess synchronously. This method is intended to be run in a separate thread.
        """
        import subprocess 
        process = subprocess.Popen(
            command,
            cwd=str(self.repo_path.resolve()),
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        stdout, stderr = process.communicate()
        return process.returncode, stdout, stderr
