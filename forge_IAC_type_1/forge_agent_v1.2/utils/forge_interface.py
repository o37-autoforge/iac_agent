# forge_agent/forge_interface.py

import logging
import re

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


def strip_ansi_codes(text):
    ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
    return ansi_escape.sub('', text)

def remove_consecutive_duplicates(text):
    lines = text.split('\n')
    new_lines = []
    prev_line = None
    for line in lines:
        if line != prev_line:
            new_lines.append(line)
            prev_line = line
    return '\n'.join(new_lines)

def identify_tool_from_command(command: str) -> str:
    """
    Identifies the tool associated with a test command.
    """
    tool_keywords = {
        'terraform': ['terraform'],
        'ansible': ['ansible', 'ansible-playbook'],
        'puppet': ['puppet'],
        'chef': ['chef'],
        'docker': ['docker', 'docker-compose'],
    }

    command_lower = command.lower()
    for tool, keywords in tool_keywords.items():
        for keyword in keywords:
            if keyword in command_lower:
                return tool
    return None

def is_input_prompt(output_line: str) -> bool:
    """
    Determines if the output line is prompting for user input.
    """
    prompt_patterns = [
        "Enter a value",
    ]
    return any(pattern.lower() in strip_ansi_codes(output_line).lower() for pattern in prompt_patterns)

def remove_consecutive_duplicates(text: str) -> str:
    """
    Removes consecutive duplicate lines from the text.

    :param text: The text containing potential duplicated lines.
    :return: Cleaned text without consecutive duplicate lines.
    """
    lines = text.splitlines()
    cleaned_lines = []
    previous_line = None
    for line in lines:
        if line != previous_line:
            cleaned_lines.append(line)
            previous_line = line
    return '\n'.join(cleaned_lines)

def sanitize_filename(filename: str) -> str:
    """
    Sanitizes the filename by removing or replacing invalid characters.

    :param filename: The original filename.
    :return: Sanitized filename.
    """
    valid_chars = f"-_.() {string.ascii_letters}{string.digits}"
    sanitized = ''.join(c for c in filename if c in valid_chars)
    sanitized = sanitized.replace(' ', '_')  # Replace spaces with underscores
    return sanitized[:255]  # Limit filename length

def clean_response(text: str) -> str:
    """
    Cleans response text by removing escape characters and normalizing newlines.

    :param text: The text to clean.
    :return: Cleaned text.
    """
    # Remove escape characters
    text = text.encode('utf-8').decode('unicode_escape')
    # Normalize newlines
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    # Remove consecutive newlines
    text = remove_consecutive_duplicates(text)
    return text.strip()

def clean_forge_response(response: str) -> str:
    """Clean up forge response by removing formatting and session info."""
    # If response contains a question and answer, extract just the answer
    if '\n\n' in response:
        _, answer = response.split('\n\n', 1)
    else:
        answer = response

    if '\n' in response:
        _, answer = response.split('\n', 1)
    else:
        answer = response
        
    # Remove token counts and session info
    if 'Tokens:' in answer:
        answer = answer.split('Tokens:')[0]
        
    # Remove the separator line
    if 'â\x94\x80â\x94\x80' in answer:
        answer = answer.split('â\x94\x80')[0]
        
    # Clean up whitespace
    answer = answer.strip()
    
    return answer


