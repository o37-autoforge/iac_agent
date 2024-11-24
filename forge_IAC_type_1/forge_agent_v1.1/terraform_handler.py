# forge_agent/terraform_handler.py

import asyncio
import json
import logging
from pathlib import Path
import subprocess

logger = logging.getLogger(__name__)
def disable_logging():
    original_log_handlers = logging.getLogger().handlers[:]
    for handler in original_log_handlers:
        logging.getLogger().removeHandler(handler)

disable_logging()

class TerraformHandler:
    def __init__(self, repo_path: Path):
        self.repo_path = repo_path

    async def run_terraform_command(self, command: list) -> dict:
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
            if plan_json.get('resource_changes'):
                logger.info("Plan has changes and aligns with the query.")
                return True
            else:
                logger.warning("Plan does not have changes or does not align with the query.")
                return False

        except Exception as e:
            logger.error(f"Error analyzing Terraform plan: {e}")
            return False

    async def cleanup_terraform_files(self):
        try:
            plan_file = self.repo_path / "plan.out"
            if plan_file.exists():
                plan_file.unlink()
                logger.info("Removed Terraform plan file.")
        except Exception as e:
            logger.error(f"Failed to clean up Terraform files: {e}")
