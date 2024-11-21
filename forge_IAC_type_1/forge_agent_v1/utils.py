# forge_agent/utils.py

import re
import string

def strip_ansi_codes(text: str) -> str:
    """
    Removes ANSI escape sequences from the given text.

    :param text: The text containing ANSI escape sequences.
    :return: Cleaned text without ANSI codes.
    """
    ansi_escape = re.compile(r'\x1B[@-_][0-?]*[ -/]*[@-~]')
    return ansi_escape.sub('', text)

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

