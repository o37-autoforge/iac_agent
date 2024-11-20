# agent.py

import asyncio
import json
import os
import logging
import traceback
import sys
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Optional
from dotenv import load_dotenv
from .githubHandler import GitHandler
from .models import (
    forgeQuestions,
    UserQuestion,
    UserResponse,
)
import re
from .utils import strip_ansi_codes, remove_consecutive_duplicates
from .llm_handler import LLMHandler
from .subprocess_handler import SubprocessHandler
import git
import warnings
from langchain_core.globals import set_verbose, set_debug

# Disable verbose logging
set_verbose(False)

# Disable debug logging
set_debug(False)

# Suppress all warnings
warnings.filterwarnings("ignore")
warnings.filterwarnings("ignore", module="langchain")
warnings.filterwarnings("ignore", category=DeprecationWarning)

import logging

logging.getLogger().setLevel(logging.CRITICAL)
class forgeAgent:
    def __init__(self):
                # Initialize LLM Handler
        self.git_handler = GitHandler()
        self.repo = self.git_handler.clone_repository()
        self.repo_path = self.git_handler.repo_path

        self.llm_handler = LLMHandler(repo_path=str(self.repo_path))

        # Prepare the logs directory
        self.logs_dir = self.repo_path / "forge_logs"
        self.logs_dir.mkdir(exist_ok=True)
        # logger.info(f"Logs will be saved to directory: {self.logs_dir}")

        self.user_responses: List[UserResponse] = []  # To store user responses
        self.forge_responses: Dict[str, str] = {}     # To store forge responses

        # Path to the status log file
        self.status_log_file = self.logs_dir / "status_log.txt"

        asyncio.run(self.status_update(f"Creating a map of your codebase. This may take a while..."))

        # Load environment variables
        load_dotenv()
        OPEN_AI_KEY = os.getenv("OPENAI_API_KEY")
        if not OPEN_AI_KEY:
            # logger.error("OPENAI_API_KEY is not set in environment variables.")
            raise EnvironmentError("OPENAI_API_KEY is missing.")

        # Initialize GitHandler and clone the repository
        try:
            self.repo.git.checkout(self.git_handler.branch_name)
            # logger.info(f"Checked out to existing branch '{self.git_handler.branch_name}'")
            
        except git.exc.GitCommandError:
            # Branch doesn't exist; create it
            self.repo.git.checkout('-b', self.git_handler.branch_name)
            # logger.info(f"Created and checked out to new branch '{self.git_handler.branch_name}'")

        # Initialize Subprocess Handler and start forge
        self.subprocess_handler = SubprocessHandler(self.repo_path)
        try:
            self.subprocess_handler.start_forge(OPEN_AI_KEY, self.get_repo_content())
        except Exception as e:
            # logger.error(f"Failed to start forge: {str(e)}")
            raise

    def get_repo_content(self) -> List[Path]:
        """
        Collects and returns the relative paths of relevant IaC and CI/CD files from the repository.
        """
        relevant_files = []
        
        try:
            # Define file patterns for IaC and CI/CD tools
            iac_file_patterns = [
                '*.tf', '*.tfvars', '*.hcl',                 # Terraform
                '*.yaml', '*.yml', '*.json',                 # CloudFormation, Ansible, etc.
                '*.pp',                                      # Puppet
                '*.rb',                                      # Chef
                'Dockerfile',                                # Docker
                'docker-compose.yml', 'docker-compose.yaml'  # Docker Compose
            ]
            cicd_file_patterns = [
                '.github/workflows/*.yml', '.github/workflows/*.yaml',  # GitHub Actions
                '.circleci/config.yml',                                  # CircleCI
                '.travis.yml',                                           # Travis CI
                'Jenkinsfile',                                           # Jenkins
                '.gitlab-ci.yml',                                        # GitLab CI
                'azure-pipelines.yml',                                   # Azure Pipelines
                'appveyor.yml',                                          # AppVeyor
                'bitbucket-pipelines.yml',                               # Bitbucket Pipelines
                '.drone.yml',                                            # Drone CI
                'teamcity-settings.kts'                                  # TeamCity
            ]

            # Extract file names without patterns for faster lookup using sets
            iac_file_names = set(pattern for pattern in iac_file_patterns if '/' not in pattern and '*' not in pattern)
            cicd_file_names = set(pattern for pattern in cicd_file_patterns if '/' not in pattern and '*' not in pattern)

            # Patterns that include directories or wildcards
            iac_file_patterns_with_glob = [pattern for pattern in iac_file_patterns if '/' in pattern or '*' in pattern]
            cicd_file_patterns_with_glob = [pattern for pattern in cicd_file_patterns if '/' in pattern or '*' in pattern]

            # Combine all patterns for easier checking
            all_patterns_with_glob = iac_file_patterns_with_glob + cicd_file_patterns_with_glob

            # Walk through the repository
            for root, dirs, files in os.walk(self.repo_path):
                for file in files:
                    file_path = Path(root) / file
                    relative_path = file_path.relative_to(self.repo_path)

                    # First, check if the file name is in the sets of IaC or CI/CD file names
                    if file in iac_file_names or file in cicd_file_names:
                        relevant_files.append(relative_path)
                        continue  # Skip to next file to prevent duplicates

                    # Then, check if the relative path matches any of the glob patterns
                    if any(relative_path.match(pattern) for pattern in all_patterns_with_glob):
                        relevant_files.append(relative_path)
                        continue  # Skip to next file to prevent duplicates

            if not relevant_files:
                # logger.info("No relevant IaC or CI/CD files found in the repository.")
                return []
            else:
                return relevant_files

        except Exception as e:
            # logger.error(f"Error reading repository content: {e}")
            return []


    async def status_update(self, stage_description: str):
        # Add status update for adding files to context
        status_message = await self.llm_handler.generate_status_message(stage_description)
        self.append_status_to_file(status_message)


    def append_status_to_file(self, status_message: str):
        # print(f"appending status: {status_message}")
        """Appends a status message to the status log file."""
        with open(self.status_log_file, 'a') as f:
            f.write(f"{datetime.now()}: {status_message}\n")

    async def set_forge_mode(self, mode: str) -> bool:
        """
        Changes forge's mode between 'ask' and 'code'

        :param mode: Either 'ask' or 'code'
        :return: Boolean indicating success
        """
        try:
            if mode not in ['ask', 'code']:
                raise ValueError(f"Invalid mode: {mode}. Must be 'ask' or 'code'")

            # logger.info(f"Changing forge mode to: {mode}")
            self.subprocess_handler.child.sendline(f"/chat-mode {mode}")

            # Expect the new prompt based on mode
            starter = "ask" if mode == "ask" else ""
            expected_prompt = f"{starter}>"
            self.subprocess_handler.child.expect(expected_prompt, timeout=60)

            # logger.info(f"Successfully changed to {mode} mode")
            return True

        except Exception as e:
            # logger.error(f"Failed to set forge mode to {mode}: {e}")
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

            response = await self.subprocess_handler.send_command(question)
            cleaned_response = strip_ansi_codes(response)
            cleaned_response = remove_consecutive_duplicates(cleaned_response)
            # logger.debug(f"forge response: {cleaned_response}")
            return cleaned_response.strip()

        except Exception as e:
            # logger.error(f"Failed to ask forge question: {e}")
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
            
            response = await self.subprocess_handler.send_command(command)
            await self.subprocess_handler.send_command("/commit")
            cleaned_response = strip_ansi_codes(response)
            cleaned_response = remove_consecutive_duplicates(cleaned_response)
            # logger.debug(f"forge response: {cleaned_response}")
            return cleaned_response.strip()
            
        except Exception as e:
            # logger.error(f"Failed to send code command: {e}")
            raise

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

    async def execute_subtask(self, task: str) -> bool:
        """
        Executes a single subtask using forge in code mode
        """
        # logger.info(f"Executing subtask {task}")                    
        # Send the actual query to forge
        response = await self.send_code_command(task)
            
        # Check if there were any errors in the response
        if "error" in response.lower() or "failed" in response.lower():
            # logger.error(f"forge reported an error for subtask {task}: {response}")
            return False
                
        # logger.info(f"Successfully completed subtask {task}")
        return True
    

    async def handle_uploaded_file(self, file_content: bytes, filename: str) -> None:
        """
        Handles a file uploaded by the user.

        :param file_content: The content of the uploaded file.
        :param filename: The name of the uploaded file.
        """
        # Save the file to a path within the repository
        file_path = self.repo_path / filename
        try:
            with open(file_path, 'wb') as f:
                f.write(file_content)
            # logger.info(f"Saved uploaded file to {file_path}")
        except Exception as e:
            # logger.error(f"Error saving uploaded file: {str(e)}")
            raise

        asyncio.run(self.status_update(f"Learning about the documentation file {filename}"))

        # Pass the file to forge agent by executing /read file_path
        try:
            # Ensure we're in 'code' mode
            if not await self.set_forge_mode('code'):
                raise Exception("Failed to set code mode")

            # Send /read filename command to forge
            command = f"/read {filename}"
            response = await self.subprocess_handler.send_command(command)
            cleaned_response = strip_ansi_codes(response)
            cleaned_response = remove_consecutive_duplicates(cleaned_response)
            # logger.debug(f"forge response to /read: {cleaned_response}")
            print(f"\nProcessed the uploaded file '{filename}' with forge.")
        except Exception as e:
            # logger.error(f"Error reading file in forge agent: {str(e)}")
            raise

    async def handle_user_interaction(self):
        """Handles the interaction with the user and task execution"""
        # Generate varied intro message via LLM
        intro_message = await self.llm_handler.generate_intro_message()
        print(intro_message)
        print("You can type 'exit' at any time to quit.\n")

        while True:
            user_query = input("Your request: ").strip()
            if not user_query:
                # logger.warning("Empty query provided.")
                print("Please enter a valid request.\n")
                continue
            if user_query.lower() in ['exit', 'quit']:
                # logger.info("Exiting forge Agent as per user request.")
                await self.close_forge()
                print("Goodbye!")
                break

            await self.status_update(f"Understanding your query")        

            # Ask the user if they want to upload a file
            upload_choice = input("Would you like to upload a documentation file? (y/n): ").lower()
            if upload_choice == 'y':
                # Simulate file upload (in actual implementation, this would come from the frontend)
                file_path = input("Please provide the path to the file you'd like to upload: ").strip()
                if not file_path or not os.path.isfile(file_path):
                    print("Invalid file path. Skipping file upload.")
                else:
                    filename = os.path.basename(file_path)
                    with open(file_path, 'rb') as f:
                        file_content = f.read()
                    await self.handle_uploaded_file(file_content, filename)
            await self.status_update(f"I have some questions about your request.")        

            # Process initial query and get user responses
            questions = await self.generate_questions_for_user(user_query)
            self.user_responses = await self.ask_user_questions(questions)

            # Get well-written query for forge
            await self.status_update(f"Understanding your responses to my questions")

            forge_query = self.llm_handler.generate_forge_query(
                user_query,
                [resp.model_dump() for resp in self.user_responses]
            )

            await self.status_update(f"Great! I'm now working on implementing your request")

            # Send query to forge
            await self.execute_subtask("\\architect " + forge_query)
            
            await self.status_update(f"Finished implementing your request! Make sure to review my work.")

            # Ask the user if they want to continue, review, or create a PR
            while True:
                next_action = input("\nWhat would you like to do next? (c)ontinue, (r)eview changes, (p)ush and create PR, or (e)xit: ").lower()
                if next_action == 'c':
                    # Continue with another query
                    break  # Break inner loop to input a new query
                elif next_action == 'r':
                    # Review changes (e.g., show git diff)
                    print("\nHere are the changes made so far:\n")
                    diff = self.repo.git.diff()
                    print(diff)
                elif next_action == 'p':
                    # Push changes and create PR
                    await self.push_changes_and_create_pr(user_query)
                    await self.close_forge()
                    print("I've pushed the changes and created a pull request for you.")
                    return self.repo
                elif next_action == 'e' or next_action == 'exit':
                    await self.close_forge()
                    print("Goodbye!")
                    return
                else:
                    print("Sorry, I didn't understand that option. Please enter 'c', 'r', 'p', or 'e'.")

    async def push_changes_and_create_pr(self, commit_message: str):
        """
        Adds, commits, and pushes changes to the repository, then creates a PR.
        """
        self.repo.git.add(A=True)
        self.repo.index.commit(commit_message)
        origin = self.repo.remote(name='origin')
        origin.push(self.git_handler.branch_name)
        # logger.info(f"Pushed changes to branch '{self.git_handler.branch_name}'")

        # Create a pull request
        pr = self.git_handler.create_pull_request(
            title=commit_message,
            body="This PR includes code changes made by the forge agent."
        )
        # logger.info(f"Pull request created: {pr.html_url}")
        print(f"\nA pull request has been created: {pr.html_url}")

    async def close_forge(self):
        """
        Closes the forge process gracefully.
        """
        try:
            self.subprocess_handler.close_forge()
        except Exception as e:
            # logger.error(f"Error while closing forge: {str(e)}")
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