# forge_agent/forge_interface.py

import logging

from .utils import strip_ansi_codes, remove_consecutive_duplicates

logger = logging.getLogger(__name__)
def disable_logging():
    original_log_handlers = logging.getLogger().handlers[:]
    for handler in original_log_handlers:
        logging.getLogger().removeHandler(handler)

disable_logging()

class ForgeInterface:
    def __init__(self, subprocess_handler):
        self.subprocess_handler = subprocess_handler

    async def set_forge_mode(self, mode: str) -> bool:
        try:
            if mode not in ['ask', 'code']:
                raise ValueError(f"Invalid mode: {mode}. Must be 'ask' or 'code'")

            logger.info(f"Changing forge mode to: {mode}")
            self.subprocess_handler.child.sendline(f"/chat-mode {mode}")

            starter = "ask" if mode == "ask" else ""
            expected_prompt = f"{starter}>"
            self.subprocess_handler.child.expect(expected_prompt, timeout=60)

            logger.info(f"Successfully changed to {mode} mode")
            return True

        except Exception as e:
            logger.error(f"Failed to set forge mode to {mode}: {e}")
            return False

    async def ask_forge_question(self, question: str) -> str:
        try:
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
        try:
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

    async def execute_subtask(self, task: str) -> bool:
        logger.info(f"Executing subtask {task}")
        response = await self.send_code_command(task)

        if "error" in response.lower() or "failed" in response.lower():
            logger.error(f"forge reported an error for subtask {task}: {response}")
            return False

        logger.info(f"Successfully completed subtask {task}")
        return True

    async def close_forge(self):
        try:
            self.subprocess_handler.close_forge()
        except Exception as e:
            logger.error(f"Error while closing forge: {str(e)}")
            print(f"An error occurred while closing forge: {str(e)}")
