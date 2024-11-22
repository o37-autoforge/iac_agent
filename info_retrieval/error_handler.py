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

    async def handle_validation_error(self, output: str, expected_output: str, filename: str):
        prompt = f"After running the code in {filename}, I have the following output: {output}. I was expecting the following output: {expected_output}. Please fix the code."
        await self.forge_interface.execute_subtask(prompt)
    
    async def handle_syntax_error(self, error_message: str, expected_output: str, filepath: str):

        with open(filepath, 'r') as file:
            code = file.read()
            prompt = f"The error message is: {error_message}. The expected output is: {expected_output}. The code is: {code}. Please fix the code. "

        logger.info("Sending error back to forge for resolution.")
        await self.forge_interface.execute_subtask(prompt)


    async def prompt_user_for_manual_edits(self):
        print("\nWe've encountered persistent issues while applying your IaC changes.")
        print("Please review and make necessary edits to the repository manually.")
        logger.info("User prompted for manual repository edits.")
