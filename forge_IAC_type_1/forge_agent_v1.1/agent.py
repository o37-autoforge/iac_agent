# forge_agent/main.py

import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import List

from dotenv import load_dotenv

from .aws_handler import AWSHandler
from .error_handler import ErrorHandler
from .forge_interface import ForgeInterface
from .github_handler import GitHandler
from .llm_handler import LLMHandler
from .models import UserResponse
from .subprocess_handler import SubprocessHandler
from .test_runner import TestRunner
from .user_interaction import UserInteraction
from .utils import identify_tool_from_command, strip_ansi_codes
import fnmatch
from typing import Optional
from datetime import datetime
from typing import Dict

logger = logging.getLogger(__name__)

def disable_logging():
    original_log_handlers = logging.getLogger().handlers[:]
    for handler in original_log_handlers:
        logging.getLogger().removeHandler(handler)

disable_logging()


class forgeAgent:
    def __init__(self, applyChanges=True, autoPR=True):
        load_dotenv()
        self.applyChanges = applyChanges
        self.autoPR = autoPR
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
        except Exception:
            self.repo.git.checkout('-b', self.git_handler.branch_name)
            logger.info(f"Created and checked out to new branch '{self.git_handler.branch_name}'")

        # Prepare the logs directory
        self.logs_dir = self.repo_path / "forge_logs"
        self.logs_dir.mkdir(exist_ok=True)
        # logger.info(f"Logs will be saved to directory: {self.logs_dir}")

        self.user_responses: List[UserResponse] = []  # To store user responses
        self.forge_responses: Dict[str, str] = {}     # To store forge responses

        # Path to the status log file
        self.status_log_file = self.logs_dir / "status_log.txt"

        # Initialize other handlers
        self.aws_handler = AWSHandler()
        self.llm_handler = LLMHandler(repo_path=str(self.repo_path))
        asyncio.run(self.status_update("Mapping your codebase. This may take a while..."))
        self.subprocess_handler = SubprocessHandler(self.repo_path)
        self.forge_interface = ForgeInterface(self.subprocess_handler)
        self.error_handler = ErrorHandler(self.forge_interface)
        self.user_interaction = UserInteraction(
            self.llm_handler, self.subprocess_handler, self.repo_path / "forge_logs", self.git_handler
        )
        self.test_runner = TestRunner(
            self.repo_path, identify_tool_from_command, self.find_tool_directory, self.error_handler.handle_error, self.status_update
        )

        self.user_responses: List[UserResponse] = []
        self.forge_responses = {}
        self.max_retries = 3
        self.subprocess_handler.start_forge(OPEN_AI_KEY, self.get_repo_content())


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


    def find_tool_directory(self, tool_name: str) -> Optional[Path]:
        
        tool_file_patterns = {
            'terraform': ['*.tf', '*.tfvars', '*.hcl'],
            'ansible': ['*.yaml', '*.yml'],
            'puppet': ['*.pp'],
            'chef': ['*.rb'],
            'docker': ['Dockerfile', 'docker-compose.yml', 'docker-compose.yaml'],
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

    async def handle_query(self):
        intro_message = await self.llm_handler.generate_intro_message()
        print(intro_message)
        # print("You can type 'exit' at any time to quit.\n")

        await self.status_update(f"Understanding your query")        

        result = await self.user_interaction.handle_user_interaction()
        if not result:
            return False
        user_query, starting_query = result
        self.user_query = user_query
        self.starting_query = starting_query

        await self.status_update(f"Im ready to go! I'm now working on implementing your request")        


        await self.forge_interface.execute_subtask("\\architect " + starting_query + "Focus on sticking with the existing codebase, and not adding any new tools. For example, if a user is using terraform, dont use python to implement the changes. ")

        """ 
        Add another layer of testing here. I want to use an LLM to validate whether or not the 
        changes made by the IaC agent match up properly with the user's request. 
        
        """

        await self.status_update(f"I've implemented your request. I'm now testing my work.")        

        tests = self.llm_handler.generate_test_functions(self.user_query, self.starting_query)

        passed_tests, failed_test, error_message = await self.test_runner.automate_testing_workflow(
            [i.test for i in tests]
        )

        await self.status_update(f"I'm now applying the changes I made to your codebase to your cloud environment.")  

        if self.applyChanges:
            commands = self.llm_handler.generate_apply_functions(
                self.starting_query, " ".join([i.test for i in tests])
            )

            passed_tests, failed_test, error_message = await self.test_runner.automate_apply_workflow(
                [i.command for i in commands]
            )

        await self.status_update(f"Updating gitignore and comitting changes")        

        self.update_gitignore()

        self.repo.git.add(A=True)
        self.repo.index.commit(user_query)
        origin = self.repo.remote(name='origin')
        origin.push(self.git_handler.branch_name)

        await self.status_update(f"Opening a PR to the {self.git_handler.main_branch} branch of your repository")        

        if self.autoPR:
            pr = self.git_handler.create_pull_request(
                title=user_query,
                body="This PR adds forge's changes to satisfy your query to the repository."
            )
            logger.info(f"Pull request created: {pr.html_url}")
            print(f"Pull request created: {pr.html_url}")

        return self.repo

    def update_gitignore(self):
        gitignore_path = self.repo_path / '.gitignore'
        entries_to_add = ['.terraform/', 'plan.out']

        if not gitignore_path.exists():
            with open(gitignore_path, 'w') as f:
                f.write('\n'.join(entries_to_add) + '\n')
            logger.info(f"Created .gitignore file with entries: {', '.join(entries_to_add)}")
        else:
            with open(gitignore_path, 'r') as f:
                existing_entries = f.read().splitlines()

            with open(gitignore_path, 'a') as f:
                for entry in entries_to_add:
                    if entry not in existing_entries:
                        f.write(entry + '\n')
                        logger.info(f"Added '{entry}' to .gitignore")
                    else:
                        logger.info(f"Entry '{entry}' already exists in .gitignore")
