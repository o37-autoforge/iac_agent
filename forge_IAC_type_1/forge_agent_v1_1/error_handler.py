# forge_agent/error_handler.py

import logging

logger = logging.getLogger(__name__)
def disable_logging():
    original_log_handlers = logging.getLogger().handlers[:]
    for handler in original_log_handlers:
        logging.getLogger().removeHandler(handler)

disable_logging()

class ErrorHandler:
    def __init__(self, forge_interface):
        self.forge_interface = forge_interface

    async def handle_error(self, error_message: str, command: str):
        logger.info("Sending error back to forge for resolution.")
        await self.forge_interface.execute_subtask(
            f"Please solve this error that stemmed from this command: {command}. The error was: {error_message}"
        )

    async def prompt_user_for_manual_edits(self):
        print("\nWe've encountered persistent issues while applying your IaC changes.")
        print("Please review and make necessary edits to the repository manually.")
        logger.info("User prompted for manual repository edits.")
