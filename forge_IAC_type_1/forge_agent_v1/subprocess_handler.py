# forge_agent/subprocess_handler.py

import os
import pexpect
import asyncio
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

class SubprocessHandler:
    def __init__(self, repo_path: Path):
        self.repo_path = repo_path
        self.child = None

    def start_forge(self, openai_api_key: str):
        """
        Starts the forge process using pexpect and waits for the initial prompt.
        """
        combined_command = f"export OPENAI_API_KEY={openai_api_key} TERM=dumb && forge --yes"
        logger.info(f"Starting forge with command: {combined_command}")

        try:
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

            # Add Terraform files to context
            repo_files = list(self.repo_path.glob("**/*.tf"))
            if repo_files:
                for file in repo_files:
                    relative_path = file.relative_to(self.repo_path)
                    self.child.sendline(f"/add {relative_path}")
                    self.child.expect(">", timeout=60)
                    logger.info(f"Added file to context: {relative_path}")

        except pexpect.TIMEOUT:
            logger.error("Timed out waiting for forge to start.")
            if self.child:
                self.child.close(force=True)
            raise
        except pexpect.EOF:
            logger.error("forge terminated unexpectedly during startup.")
            raise
        except Exception as e:
            logger.error(f"Failed to initialize forge: {str(e)}")
            if self.child:
                self.child.close(force=True)
            raise

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