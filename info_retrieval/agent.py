import os
import sys
import asyncio
import logging
import subprocess
from pathlib import Path
from typing import List

from dotenv import load_dotenv

from llm_handler import LLMHandler
from models import Subtask, Plan
from typing import Dict, List
from aws_handler import AWSHandler
from subprocess_handler import SubprocessHandler
from forge_interface import ForgeInterface
from error_handler import ErrorHandler
from .utils import strip_ansi_codes

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class InfoRetrievalAgent:
    def __init__(self):
        load_dotenv()
        self.llm_handler = LLMHandler()
        self.code_dir = Path("generated_code")
        self.code_dir.mkdir(exist_ok=True)
        self.max_retries = 6
        
        OPEN_AI_KEY = os.getenv("OPENAI_API_KEY")
        self.OPEN_AI_KEY = OPEN_AI_KEY
        if not OPEN_AI_KEY:
            logger.error("OPENAI_API_KEY is not set in environment variables.")
            raise EnvironmentError("OPENAI_API_KEY is missing.")

        self.logs_dir = self.code_dir / "forge_logs"
        self.logs_dir.mkdir(exist_ok=True)
        # logger.info(f"Logs will be saved to directory: {self.logs_dir}")
        # Path to the status log file
        self.status_log_file = self.logs_dir / "status_log.txt"

        # Initialize other handlers
        self.aws_handler = AWSHandler()
        self.llm_handler = LLMHandler(repo_path=str(self.code_dir))
        asyncio.run(self.status_update("Mapping your codebase. This may take a while..."))
        self.subprocess_handler = SubprocessHandler(self.code_dir)
        self.forge_interface = ForgeInterface(self.subprocess_handler)
        self.error_handler = ErrorHandler(self.forge_interface)
        self.forge_responses = {}
        self.max_retries = 3
        self.error_handler = ErrorHandler(self.forge_interface)

    async def generate_plan(self, user_query: str) -> Plan:
        """Generates a plan consisting of multiple subtasks."""
        plan = await self.llm_handler.generate_plan(user_query)
        return plan

    async def execute_part(self, subtask: Subtask):
        filename = subtask.filename
        task = subtask.implementation_outline
        validation = subtask.expected_output_description

        # Generate code from the implementation outline
        response = await self.forge_interface.execute_subtask("\\architect " + task)

        while retries < self.max_retries:

            # Attempt to run the file
            try:
                result = subprocess.run(
                    [sys.executable, str(self.code_dir / subtask.filename)],
                    capture_output=True,
                    text=True
                )

                stdout, stderr = await result.communicate()
                stdout_text = strip_ansi_codes(stdout.decode().strip())
                stderr_text = strip_ansi_codes(stderr.decode().strip())


                if result.returncode != 0:
                    # If there's an error, send it back to LLM for correction
                    error_message = stderr_text
                    logger.error(f"Error running {subtask.filename}: {error_message}")
                    code = await self.error_handler.handle_error(error_message, validation, self.code_dir +  "\\" + filename)
                    retries += 1

                else:
                    logger.info(f"Output from {subtask.filename}: {stdout_text}")

                    # Validate the output using LLM
                    is_valid = await self.llm_handler.validate_output(
                        stdout_text,
                        subtask.validation
                    )
                    if is_valid:
                        logger.info(f"Output from {subtask.filename} is valid.")
                        break
                    else:
                        code = await self.error_handler.handle_validation_error(stdout, validation, filename)

            except Exception as e:
                logger.error(f"Exception when running {subtask.filename}: {str(e)}")
                break  # Exit if unexpected exception occurs


        
    async def run(self):
        while True:
            """Main method to run the agent."""
            user_query = input("Hey! I am your CloudOps information retrieval agent. What do you need help with today?")

            """NEED TO ADD EXTRA AGENT TO ANSWER QUESTIONS ABOUT THE AWS THAT ARENT SUPER SPECIFIC. CAN JUST DUMP ALL INFO INTO TXT AND THEN USE GEMINI 2M CONTEXT WINDOW TO ANSWER QUESTIONS."""
            # Step 1: Generate the plan
            plan = await self.generate_plan(user_query)

            #Step 2: Startup the forge process. Creates all files and add to context
            await self.subprocess_handler.start_forge(self.OPEN_AI_KEY, [subtask.filename for subtask in plan.subtasks])

            # Step 3: Iterate through subtasks
            for subtask in plan.subtasks:
                await self.execute_part(subtask)

            # Step 5: Look at the working final subtask output, and make it output some file in a nice format.

            # Step 6: Output the file