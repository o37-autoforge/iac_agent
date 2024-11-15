import asyncio
import subprocess
import logging
from typing import List, Dict

logger = logging.getLogger(__name__)


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


    async def terraform_init(self) -> bool:
        logger.info("Running 'terraform init'")
        result = await self.run_terraform_command(['terraform', 'init'])
        if result['stderr']:
            logger.error(f"Terraform init error: {result['stderr']}")
            return False
        logger.info("Terraform init completed successfully.")
        return True

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
