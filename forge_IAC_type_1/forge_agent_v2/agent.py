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
    testCommands,
    applyCommands,
    Tests,
    Commands
)
import re
from .utils import strip_ansi_codes, remove_consecutive_duplicates 
from .llm_handler import LLMHandler
from .subprocess_handler import SubprocessHandler
import git
import subprocess
import boto3  # AWS SDK for Python
from botocore.exceptions import NoCredentialsError, PartialCredentialsError, ClientError
from dotenv import load_dotenv, set_key
from getpass import getpass
from typing import Optional
import fnmatch
import os
import pty
import asyncio
import pexpect

logger = logging.getLogger(__name__)

def remove_ansi_sequences(text):
    # Regex pattern for ANSI escape codes
    ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
    return ansi_escape.sub('', text)

class forgeAgent:
    def __init__(self, applyChanges = True, autoPR = True):
        # Load environment variables
        load_dotenv()     
        self.applyChanges = applyChanges
        self.autoPR = autoPR

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
            self.fileContext=self.subprocess_handler.start_forge(OPEN_AI_KEY, self.get_repo_content())
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

    def identify_tool_from_command(self, command: str) -> Optional[str]:
        """
        Identifies the tool associated with a test command.
        
        :param command: The test command string.
        :return: The tool name if identified, else None.
        """
        # Define keywords that identify each tool
        tool_keywords = {
            'terraform': ['terraform'],
            'ansible': ['ansible', 'ansible-playbook'],
            'puppet': ['puppet'],
            'chef': ['chef'],
            'docker': ['docker', 'docker-compose'],
            # Add more tools and their identifying keywords as needed
        }

        command_lower = command.lower()
        for tool, keywords in tool_keywords.items():
            for keyword in keywords:
                if keyword in command_lower:
                    return tool
        return None

    def find_tool_directory(self, tool_name: str) -> Optional[Path]:
        """
        Finds the directory in the repository that contains files relevant to the specified tool.
        
        :param tool_name: Name of the tool (e.g., 'terraform', 'ansible')
        :return: Path to the directory containing the tool's files, or None if not found
        """
        tool_file_patterns = {
            'terraform': ['*.tf', '*.tfvars', '*.hcl'],
            'ansible': ['*.yaml', '*.yml'],
            'puppet': ['*.pp'],
            'chef': ['*.rb'],
            'docker': ['Dockerfile', 'docker-compose.yml', 'docker-compose.yaml'],
            # Add more tools and their file patterns as needed
        }
        patterns = tool_file_patterns.get(tool_name.lower(), [])
        if not patterns:
            logger.warning(f"No file patterns defined for tool '{tool_name}'")
            return None

        for root, dirs, files in os.walk(self.repo_path):
            for file in files:
                for pattern in patterns:
                    if fnmatch.fnmatch(file, pattern):
                        directory = Path(root)
                        logger.info(f"Found '{tool_name}' files in directory: {directory}")
                        return directory
        logger.warning(f"No directory found containing '{tool_name}' files.")
        return None

    def identify_tool_from_command(self, command: str) -> str:
        """
        Identifies the tool associated with a test command.
        
        :param command: The test command string.
        :return: The tool name if identified, else None.
        """
        # Define keywords that identify each tool
        tool_keywords = {
            'terraform': ['terraform'],
            'ansible': ['ansible', 'ansible-playbook'],
            'puppet': ['puppet'],
            'chef': ['chef'],
            'docker': ['docker', 'docker-compose'],
            # Add more tools and their identifying keywords as needed
        }

        command_lower = command.lower()
        for tool, keywords in tool_keywords.items():
            for keyword in keywords:
                if keyword in command_lower:
                    return tool
        return None

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
                logger.info("No relevant IaC or CI/CD files found in the repository.")
                return []
            else:
                return relevant_files

        except Exception as e:
            logger.error(f"Error reading repository content: {e}")
            return []



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


    async def automate_testing_workflow(self, tests: List[str]) -> bool:
        """
        Automates the testing workflow by generating test commands via the LLM and executing them.
        
        :return: True if the workflow succeeds, False otherwise.
        """
        print(f"Running tests... {tests}")

        
        passed_tests, failed_test, error_message = await self.run_tests(tests)
        
        return passed_tests, failed_test, error_message

    from typing import Tuple, List, Optional

    import asyncio
    import os
    import pty
    from typing import List, Tuple, Optional
    import logging

    logger = logging.getLogger(__name__)

    async def run_tests(self, tests: List[str]) -> Dict[str, Optional[object]]:
        """
        Executes the test commands generated by the LLM with a retry mechanism.
        
        :param tests: List of test commands to execute.
        :return: Dictionary containing:
                - 'successful_commands': List of commands that executed successfully.
                - 'failed_command': The command that failed after retries (if any).
                - 'error': The error message associated with the failed command (if any).
        """
        logger.info("Running generated test commands.")
        
        successful_commands = []
        failed_command = None
        error_message = None
        max_retries = 3  # Maximum number of retries per command

        for test_command in tests:
            if not test_command:
                logger.error("Test command is missing in the test object.")
                continue

            # Determine the tool associated with the command
            tool = self.identify_tool_from_command(test_command)
            if not tool:
                logger.warning(f"Could not identify tool for command: {test_command}")
                cwd = str(self.repo_path.resolve())
            else:
                # Find the directory for the tool
                tool_directory = self.find_tool_directory(tool)
                if tool_directory:
                    cwd = str(tool_directory.resolve())
                    logger.info(f"Setting cwd to '{cwd}' for tool '{tool}'")
                else:
                    logger.warning(f"No directory found for tool '{tool}'. Using repository root.")
                    cwd = str(self.repo_path.resolve())

            logger.info(f"Preparing to execute test command: '{test_command}' in directory: '{cwd}'")

            # Initialize retry counter
            retry_count = 0
            while retry_count < max_retries:
                logger.info(f"Attempt {retry_count + 1} for command: '{test_command}'")
                process = await asyncio.create_subprocess_shell(
                test_command,
                    cwd=cwd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=os.environ.copy()
                )
                stdout, stderr = await process.communicate()
                stdout_text = strip_ansi_codes(stdout.decode().strip())
                stderr_text = strip_ansi_codes(stderr.decode().strip())

                logger.debug(f"Test command stdout: {stdout_text}")
                logger.debug(f"Test command stderr: {stderr_text}")

                if process.returncode == 0:
                    logger.info(f"Test command succeeded: '{test_command}'")
                    print(f"[Test Passed] {test_command}")
                    successful_commands.append(test_command)
                    break  # Exit the retry loop and proceed to the next command
                else:
                    retry_count += 1
                    await self.handle_error(stderr_text, test_command)
                    logger.error(f"Test command '{test_command}' failed with error: {stderr_text}")
                    if retry_count < max_retries:
                        logger.info(f"Retrying command '{test_command}' (Attempt {retry_count + 1}/{max_retries})...")
                    else:
                        logger.error(f"Command '{test_command}' failed after {max_retries} attempts.")

                # After retries, check if the command was successful
            if retry_count == max_retries and process.returncode != 0:
                print("I need help from you!!")

                return successful_commands, test_command, stderr_text
                
        return successful_commands, None, None
             

    def interact_with_subprocess(self, child):
        try:
            while True:
                index = child.expect([
                    r'Enter a value.*?:',
                    r'Error:.*',
                    r'Do you want to perform these actions.*\?',
                    pexpect.EOF,
                    pexpect.TIMEOUT
                ], timeout=10)

                if index == 0:
                    # Prompt for input
                    prompt = child.before + child.after
                    user_input = input(f"Subprocess is requesting input: {prompt}\nYour input: ").strip()
                    child.sendline(user_input)
                elif index == 1:
                    # Error detected
                    error_message = child.before + child.after
                    logger.error(f"Error in subprocess: {error_message}")
                    return False
                elif index == 2:
                    # Confirmation prompt
                    child.sendline('yes')
                elif index == 3:
                    # EOF
                    break
                elif index == 4:
                    # Timeout
                    continue


                return True
            
        except Exception as e:
        
            logger.error(f"Error interacting with subprocess: {e}")
            return False


    def is_input_prompt(self, output_line: str) -> bool:
        """
        Determines if the output line is prompting for user input.

        :param output_line: A line of output from the subprocess.
        :return: True if an input prompt is detected, False otherwise.
        """
        # Define patterns that indicate an input prompt
        prompt_patterns = [
            "Enter a value",
         ]
        return any(pattern.lower() in remove_ansi_sequences(output_line).lower() for pattern in prompt_patterns)

    async def prompt_user_for_input(self, prompt_message: str) -> str:
        """
        Prompts the user for input based on the message from the subprocess.

        :param prompt_message: The prompt message displayed by the subprocess.
        :return: The user's input as a string.
        """
        print(f"\nSubprocess is requesting input: {prompt_message}")
        user_input = input("Your input: ").strip()
        return user_input


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
        print("HANDLING ERROR")
        """
        Sends the error message back to Aider for analysis and resolution.
        
        :param error_message: The error details to send.
        """
        logger.info("Sending error back to Aider for resolution.")

        await self.execute_subtask("Please solve this error that stemmed from this command: " + command + ". The error was:" + error_message)


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
    
    
    def update_gitignore(self):
        """
        Adds entries to the .gitignore file to ignore files generated by Terraform.
        """
        gitignore_path = self.repo_path / '.gitignore'
        entries_to_add = ['.terraform/', 'plan.out']
        
        if not gitignore_path.exists():
            # Create the .gitignore file with the required entries
            with open(gitignore_path, 'w') as f:
                f.write('\n'.join(entries_to_add) + '\n')
            logger.info(f"Created .gitignore file with entries: {', '.join(entries_to_add)}")
        else:
            # Read existing entries
            with open(gitignore_path, 'r') as f:
                existing_entries = f.read().splitlines()
            
            with open(gitignore_path, 'a') as f:
                for entry in entries_to_add:
                    if entry not in existing_entries:
                        f.write(entry + '\n')
                        logger.info(f"Added '{entry}' to .gitignore")
                    else:
                        logger.info(f"Entry '{entry}' already exists in .gitignore")


    async def handle_user_interaction(self):
        """Handles the interaction with the user and task decomposition"""
        print("Type your IaC query below. Type 'exit' to quit.\n")

        user_query = input("Enter your IaC query: ").strip()
        self.user_query = user_query
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

        # Automate testing workflow
        tests = self.llm_handler.generate_test_functions(self.user_query, self.starting_query)

        passed_tests, failed_test, error_message = await self.automate_testing_workflow([i.test for i in tests])
        
        if self.applyChanges:
            commands = self.llm_handler.generate_apply_functions(self.starting_query, " ".join([i.test for i in tests]))
        
            # Automate application workflow
            passed_tests, failed_test, error_message = await self.automate_testing_workflow([i.command for i in commands])
    
        self.update_gitignore()

        self.repo.git.add(A=True)
        self.repo.index.commit(user_query)
        origin = self.repo.remote(name='origin')
        origin.push(self.git_handler.branch_name)

        logger.info(f"Pushed changes to branch '{self.git_handler.branch_name}'")

        if self.autoPR:
            pr = self.git_handler.create_pull_request(
                title=user_query,
                body="This PR adds forge's changes to satisfy your query to the repository."
            )
            logger.info(f"Pull request created: {pr.html_url}")
            print(f"Pull request created: {pr.html_url}")

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
