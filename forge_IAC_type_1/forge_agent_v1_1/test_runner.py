# forge_agent/test_runner.py

import asyncio
import logging
import os
from typing import List, Optional

from .utils import strip_ansi_codes

logger = logging.getLogger(__name__)
def disable_logging():
    original_log_handlers = logging.getLogger().handlers[:]
    for handler in original_log_handlers:
        logging.getLogger().removeHandler(handler)

disable_logging()

class TestRunner:
    def __init__(self, repo_path, identify_tool_from_command, find_tool_directory, handle_error, status_update):
        self.repo_path = repo_path
        self.identify_tool_from_command = identify_tool_from_command
        self.find_tool_directory = find_tool_directory
        self.handle_error = handle_error
        self.status_update = status_update

    async def automate_testing_workflow(self, tests: List[str]) -> tuple:
        logger.info(f"Running tests... {tests}")
        passed_tests, failed_test, error_message = await self.run_tests(tests)
        return passed_tests, failed_test, error_message

    async def automate_apply_workflow(self, tests: List[str]) -> tuple:
        logger.info(f"Running tests... {tests}")
        passed_tests, failed_test, error_message = await self.run_changes(tests)
        return passed_tests, failed_test, error_message

    async def run_tests(self, tests: List[str]) -> dict:
        logger.info("Running generated test commands.")
        successful_commands = []
        failed_command = None
        error_message = None
        max_retries = 3

        for test_command in tests:
            if not test_command:
                logger.error("Test command is missing.")
                continue

            tool = self.identify_tool_from_command(test_command)
            if not tool:
                logger.warning(f"Could not identify tool for command: {test_command}")
                cwd = str(self.repo_path.resolve())
            else:
                tool_directory = self.find_tool_directory(tool)
                if tool_directory:
                    cwd = str(tool_directory.resolve())
                    logger.info(f"Setting cwd to '{cwd}' for tool '{tool}'")
                else:
                    logger.warning(f"No directory found for tool '{tool}'. Using repository root.")
                    cwd = str(self.repo_path.resolve())

            logger.info(f"Preparing to execute test command: '{test_command}' in directory: '{cwd}'")

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
                    await self.status_update(f"Test command '{test_command}' passed.")
                    successful_commands.append(test_command)
                    break
                else:
                    retry_count += 1
                    await self.handle_error(stderr_text, test_command)
                    logger.error(f"Test command '{test_command}' failed with error: {stderr_text}")
                    if retry_count < max_retries:
                        logger.info(f"Retrying command '{test_command}' (Attempt {retry_count + 1}/{max_retries})...")
                    else:
                        logger.error(f"Command '{test_command}' failed after {max_retries} attempts.")

            if retry_count == max_retries and process.returncode != 0:
                print("I need help from you!!")
                return successful_commands, test_command, stderr_text

        return successful_commands, None, None

    async def run_changes(self, commands: List[str]) -> tuple:
        logger.info("Running commands with restart on failure.")
        max_retries = 3
        retries = 0

        while retries < max_retries:
            all_successful = True
            for command in commands:
                logger.info(f"Executing command: '{command}'")
                tool = self.identify_tool_from_command(command)
                if not tool:
                    logger.warning(f"Could not identify tool for command: {command}")
                    cwd = str(self.repo_path.resolve())
                else:
                    tool_directory = self.find_tool_directory(tool)
                    if tool_directory:
                        cwd = str(tool_directory.resolve())
                        logger.info(f"Setting cwd to '{cwd}' for tool '{tool}'")
                    else:
                        logger.warning(f"No directory found for tool '{tool}'. Using repository root.")
                        cwd = str(self.repo_path.resolve())

                process = await asyncio.create_subprocess_shell(
                    command,
                    cwd=cwd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=os.environ.copy()
                )
                stdout, stderr = await process.communicate()
                stdout_text = strip_ansi_codes(stdout.decode().strip())
                stderr_text = strip_ansi_codes(stderr.decode().strip())

                logger.debug(f"Command stdout: {stdout_text}")
                logger.debug(f"Command stderr: {stderr_text}")

                if process.returncode == 0:
                    logger.info(f"Command succeeded: '{command}'")
                    await self.status_update(f"Application command '{command}' passed.")

                else:
                    logger.error(f"Command failed: '{command}' with error: {stderr_text}")
                    await self.handle_error(stderr_text, command)
                    all_successful = False
                    break  # Exit the for loop to restart from the beginning

            if all_successful:
                logger.info("All commands executed successfully.")
                return commands, None, None  # All commands succeeded
            else:
                retries += 1
                logger.info(f"Retrying all commands from the beginning (Attempt {retries}/{max_retries})")

        logger.error(f"Commands failed after {max_retries} attempts.")
        print("I need help from you!!")
        return [], command, stderr_text  # Return the last failed command and error message
