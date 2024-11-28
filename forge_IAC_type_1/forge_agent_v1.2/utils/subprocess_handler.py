# forge_agent/subprocess_handler.py

import os
import pexpect
import asyncio
import logging
from pathlib import Path
from typing import List
logger = logging.getLogger(__name__)
def disable_logging():
    original_log_handlers = logging.getLogger().handlers[:]
    for handler in original_log_handlers:
        logging.getLogger().removeHandler(handler)

disable_logging()

class SubprocessHandler:
    def __init__(self, repo_path: Path):
        self.repo_path = repo_path
        self.child = None

    
    def start_forge(self, openai_api_key: str, relevant_files: List[str]):
        """
        Starts the forge process using pexpect and waits for the initial prompt.
        """
        combined_command = f"export OPENAI_API_KEY={openai_api_key} TERM=dumb && forge --yes"
        logger.info(f"Starting forge with command: {combined_command}")

        
        self.child = pexpect.spawn(
                "bash",
                ["-c", combined_command],
                cwd=str(self.repo_path.resolve()),
                encoding='utf-8',
                timeout=120  # Increased timeout to accommodate startup time
            )

        expected_prompt = 'Use /help <question> for help, run "forge --help" to see cmd line arg'
        self.child.expect(expected_prompt, timeout=120)
        logger.info("forge started successfully and is ready to accept commands.")

        # Add IaC related files to context
            
        fileContext = ""

        # print(f"Relevant files: {relevant_files}")
        if relevant_files:
            for file in relevant_files:

                with open(self.repo_path / file, 'r', encoding='utf-8') as f:

                    print(f"Adding file to context: {file}")
                    self.child.sendline(f"/add {self.repo_path / file}")
                    self.child.expect(">", timeout=60)

                    content = f.read()
                    fileContext += f"=== {file} ===\n{content}\n"

        return fileContext

    def close_forge(self):
        """
        Closes the forge process gracefully.
        """
        try:
            logger.info("Sending exit command to forge.")
            self.child.sendline("/exit")
            self.child.expect(pexpect.EOF, timeout=60)
            self.child.close()

            if self.child.exitstatus != 0:
                logger.warning(f"forge exited with non-zero status: {self.child.exitstatus}")
            else:
                logger.info("forge exited successfully.")

        except pexpect.TIMEOUT:
            logger.error("Timed out waiting for forge to terminate.")
            self.child.close(force=True)
        except Exception as e:
            logger.error(f"Error while closing forge: {str(e)}")
            self.child.close(force=True)

    async def send_command(self, command: str, current_mode: str) -> str:
        """
        Sends a command to forge and returns the response.
        """
        while True:
            self.child.sendline(command.replace("\n", ""))
            logger.debug(f"Sent command to forge: {command}")

            try:
                self.child.expect([r'\b>>>>>> REPLACE\b', r'(?:\\[Yes\\]|Tokens)'], timeout=25)
            except pexpect.TIMEOUT:
                logger.error("Timed out waiting for forge response.")
                    
            response = self.child.before
            while "[YES]" in response:
                self.child.sendline("Y")
                try:
                    self.child.expect([r'\b>>>>>> REPLACE\b', r'(?:\\[Yes\\]|Tokens)'], timeout=25)
                except pexpect.TIMEOUT:
                    logger.error("Timed out waiting for forge response.")

                response = self.child.before

            break

        return response