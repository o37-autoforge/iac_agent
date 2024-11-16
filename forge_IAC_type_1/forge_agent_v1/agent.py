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
    UserQuestions,
    UserResponse,
)
import re
from .utils import strip_ansi_codes, remove_consecutive_duplicates, sanitize_filename, clean_response, clean_forge_response
from .llm_handler import LLMHandler
from .subprocess_handler import SubprocessHandler
from langchain_community.vectorstores import FAISS
from langchain_openai.embeddings import OpenAIEmbeddings
import git
import subprocess
import boto3  # AWS SDK for Python
from botocore.exceptions import NoCredentialsError, PartialCredentialsError, ClientError
from dotenv import load_dotenv, set_key
from getpass import getpass

logger = logging.getLogger(__name__)

class forgeAgent:
    def __init__(self):
        # Load environment variables
        load_dotenv()     

        # Initialize AWS Credentials
        self.ensure_aws_credentials()
        
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

        self.max_retries = 3  # Maximum number of retries for Terraform workflow

    def ensure_aws_credentials(self):
        """
        Ensures that AWS credentials are configured. If not, prompts the user to input them and saves to the .env file.
        """
        aws_access_key = os.getenv("AWS_ACCESS_KEY_ID")
        aws_secret_key = os.getenv("AWS_SECRET_ACCESS_KEY")
        aws_region = os.getenv("AWS_DEFAULT_REGION")

        missing_credentials = []
        if not aws_access_key:
            missing_credentials.append("AWS_ACCESS_KEY_ID")
        if not aws_secret_key:
            missing_credentials.append("AWS_SECRET_ACCESS_KEY")
        if not aws_region:
            missing_credentials.append("AWS_DEFAULT_REGION")

        if not missing_credentials:
            logger.info("All AWS credentials are already configured.")
            return

        print("\nAWS credentials are not fully configured. Please provide the missing credentials.")
        logger.info("Prompting user to input missing AWS credentials.")

        # Prompt user for missing credentials
        aws_credentials = {}
        if "AWS_ACCESS_KEY_ID" in missing_credentials:
            aws_credentials["AWS_ACCESS_KEY_ID"] = input("Enter your AWS Access Key ID: ").strip()
        if "AWS_SECRET_ACCESS_KEY" in missing_credentials:
            aws_credentials["AWS_SECRET_ACCESS_KEY"] = getpass("Enter your AWS Secret Access Key: ").strip()
        if "AWS_DEFAULT_REGION" in missing_credentials:
            aws_credentials["AWS_DEFAULT_REGION"] = input("Enter your AWS Default Region (e.g., us-east-1): ").strip()

        # Validate the provided credentials
        if not self.validate_aws_credentials(aws_credentials):
            logger.error("Invalid AWS credentials provided.")
            print("The AWS credentials provided are invalid. Please check and try again.")
            sys.exit(1)  # Exit the program as AWS credentials are essential

        # Save the credentials to the .env file
        self.save_aws_credentials_to_env(aws_credentials)
        print("AWS credentials have been configured successfully.\n")
        logger.info("AWS credentials have been configured and saved to .env file.")

    def validate_aws_credentials(self, credentials: Dict[str, str]) -> bool:
        """
        Validates the provided AWS credentials by making a simple AWS API call.
        
        :param credentials: Dictionary containing AWS credentials.
        :return: True if credentials are valid, False otherwise.
        """
        try:
            session = boto3.Session(
                aws_access_key_id=credentials.get("AWS_ACCESS_KEY_ID"),
                aws_secret_access_key=credentials.get("AWS_SECRET_ACCESS_KEY"),
                region_name=credentials.get("AWS_DEFAULT_REGION")
            )
            # Attempt to get the current IAM user to validate credentials
            sts = session.client('sts')
            sts.get_caller_identity()
            logger.info("AWS credentials validated successfully.")
            return True
        except (NoCredentialsError, PartialCredentialsError, ClientError) as e:
            logger.error(f"AWS credentials validation failed: {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error during AWS credentials validation: {e}")
            return False

    def save_aws_credentials_to_env(self, credentials: Dict[str, str]):
        """
        Saves the provided AWS credentials to the .env file.
        
        :param credentials: Dictionary containing AWS credentials.
        """
        env_path = Path('.env')
        for key, value in credentials.items():
            set_key(str(env_path), key, value)
        # Reload the environment variables to include the new credentials
        load_dotenv()

    async def run_terraform_command(self, command: List[str]) -> Dict[str, str]:
        """
        Runs a Terraform command asynchronously and captures its output.
        
        :param command: List of command arguments.
        :return: Dictionary with 'stdout' and 'stderr'.
        """
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                cwd=str(self.repo_path.resolve()),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await process.communicate()
            return {
                'stdout': stdout.decode().strip(),
                'stderr': stderr.decode().strip()
            }
        except Exception as e:
            logger.error(f"Error running command {' '.join(command)}: {e}")
            raise

    async def terraform_init(self) -> Dict[str, str]:
        logger.info("Running 'terraform init'")
        result = await self.run_terraform_command(['terraform', 'init'])
        if result['stderr']:
            logger.error(f"Terraform init error: {result['stderr']}")
        else:
            logger.info("Terraform init completed successfully.")
        return result


    async def terraform_plan(self) -> Dict[str, str]:
        logger.info("Running 'terraform plan'")
        result = await self.run_terraform_command(['terraform', 'plan', '-out=plan.out'])
        if result['stderr']:
            logger.error(f"Terraform plan error: {result['stderr']}")
        else:
            logger.info("Terraform plan completed successfully.")
        return result

    async def terraform_apply(self) -> Dict[str, str]:
        logger.info("Running 'terraform apply'")
        result = await self.run_terraform_command(['terraform', 'apply', 'plan.out'])
        if result['stderr']:
            logger.error(f"Terraform apply error: {result['stderr']}")
        else:
            logger.info("Terraform apply completed successfully.")
        return result

    async def analyze_plan(self) -> bool:
        """
        Analyzes the Terraform plan to ensure it aligns with the user's query.
        
        :return: True if aligned, False otherwise.
        """
        logger.info("Analyzing Terraform plan for alignment with user query.")
        try:
            process = await asyncio.create_subprocess_exec(
                'terraform', 'show', '-json', 'plan.out',
                cwd=str(self.repo_path.resolve()),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await process.communicate()
            if stderr:
                logger.error(f"Error showing Terraform plan: {stderr.decode().strip()}")
                return False

            plan_json = json.loads(stdout.decode())
            # Implement your logic to analyze the plan.
            # For simplicity, let's assume we check if there are resource changes.
            if plan_json.get('resource_changes'):
                logger.info("Plan has changes and aligns with the query.")
                return True
            else:
                logger.warning("Plan does not have changes or does not align with the query.")
                return False

        except Exception as e:
            logger.error(f"Error analyzing Terraform plan: {e}")
            return False

    async def automate_terraform_workflow(self) -> bool:
        """
        Automates the Terraform workflow: init, plan, analyze, apply.
        
        :return: True if the workflow succeeds, False otherwise.
        """
        retry_count = 0

        while retry_count < self.max_retries:
            logger.info(f"Terraform workflow attempt {retry_count + 1}")
            print(f"\n[Attempt {retry_count + 1} of {self.max_retries}] Running Terraform workflow...")

            # Step 1: terraform init
            plan_result = await self.terraform_init()
            if plan_result['stderr']:
                await self.handle_error(f"terraform init failed: {plan_result['stderr']}", command="terraform init")
                retry_count += 1
                continue

            # Step 2: terraform plan
            plan_result = await self.terraform_plan()
            if plan_result['stderr']:
                await self.handle_error(f"terraform plan failed: {plan_result['stderr']}", command="terraform plan")
                retry_count += 1
                continue

            # Step 3: Analyze the plan
            if not await self.analyze_plan():
                await self.handle_error("Terraform plan does not align with the user query.", command="terraform plan")
                retry_count += 1
                continue

            # Step 4: terraform apply
            apply_result = await self.terraform_apply()
            if apply_result['stderr']:
                await self.handle_error(f"terraform apply failed: {apply_result['stderr']}", command="terraform apply")
                retry_count += 1
                continue

            # If all steps succeeded
            logger.info("Terraform workflow completed successfully.")
            print("[Success] Terraform changes applied successfully.")
            await self.cleanup_terraform_files()
            return True

        # After max retries, prompt the user
        logger.error("Maximum retry attempts reached. Prompting user for manual intervention.")
        print("\n[Error] Maximum retry attempts reached. Please review the repository and make necessary edits manually.")
        await self.prompt_user_for_manual_edits()
        return False

    async def cleanup_terraform_files(self):
        """
        Removes Terraform plan files to clean up the repository.
        """
        try:
            plan_file = self.repo_path / "plan.out"
            if plan_file.exists():
                plan_file.unlink()
                logger.info("Removed Terraform plan file.")
        except Exception as e:
            logger.error(f"Failed to clean up Terraform files: {e}")

    async def handle_error(self, error_message: str, command: str):
        """
        Sends the error message back to Aider for analysis and resolution.
        
        :param error_message: The error details to send.
        """
        logger.info("Sending error back to Aider for resolution.")
        try:
            # Define a new prompt to send back the error
            prompt = f"""
            An error occurred during the Terraform workflow when running the command: {command}:

            "{error_message}"
            
            Please give a response that includes the error itself, and provide guidance or corrective actions to resolve it.
            """
            # Generate a response from LLM
            response_task = self.llm_handler.generate_error_query(prompt)
            # Send the response back to forge
            await self.execute_subtask(response_task)
        except Exception as e:
            logger.error(f"Failed to handle error with Aider: {e}")

    async def prompt_user_for_manual_edits(self):
        """
        Notifies the user to manually edit the repository after failed attempts.
        """
        print("\nWe've encountered persistent issues while applying your IaC changes.")
        print("Please review and make necessary edits to the repository manually.")
        logger.info("User prompted for manual repository edits.")

    async def run_subprocess(self, command: str):
        """
        Runs the subprocess synchronously. This method is intended to be run in a separate thread.
        """
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
        print("\nPlease answer the following questions about your IaC requirements:")
        
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

    def save_user_responses(self, timestamp: str, sanitized_query: str, responses: List[UserResponse]):
        """
        Saves user responses to a separate log file.
        
        :param timestamp: Timestamp string for the filename
        :param sanitized_query: Sanitized query string for the filename
        :param responses: List of UserResponse objects to save
        """
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
        print("Type your IaC query below. Type 'exit' to quit.\n")

        user_query = input("Enter your IaC query: ").strip()
        if not user_query:
            logger.warning("Empty query provided.")
            print("Please enter a valid query.\n")
            return False
        if user_query.lower() in ['exit', 'quit']:
            logger.info("Exiting forge Agent as per user request.")
            await self.close_forge()
            print("Goodbye!")
            return False

        # Process initial query and get user responses
        results = await self.generate_questions_for_user(user_query)                
        self.user_responses = await self.ask_user_questions(results)
                
        # Get well-written query for forge. 
        starting_query = self.llm_handler.generate_forge_query(user_query, [resp.model_dump() for resp in self.user_responses])

        self.starting_query = starting_query
        # Put into architect mode, and send query. Automate yesses 
        await self.execute_subtask("\\architect " + starting_query)

        # Automate Terraform workflow
        workflow_success = await self.automate_terraform_workflow()
        if not workflow_success:
            logger.error("Terraform workflow failed after maximum retries.")
            return False

        await self.close_forge()

        self.repo.git.add(A=True)
        self.repo.index.commit(user_query)
        origin = self.repo.remote(name='origin')
        origin.push(self.git_handler.branch_name)

        logger.info(f"Pushed changes to branch '{self.git_handler.branch_name}'")

        # Create a pull request
        pr = self.git_handler.create_pull_request(
            title=user_query,
            body="This PR adds forge's changes to satisfy your query to the repository."
        )

        logger.info(f"Pull request created: {pr.html_url}")

        return self.repo
    
    async def close_forge(self):
        """
        Closes the forge process gracefully.
        """
        try:
            self.subprocess_handler.close_forge()
        except Exception as e:
            logger.error(f"Error while closing forge: {str(e)}")
            print(f"An error occurred while closing forge: {str(e)}")
