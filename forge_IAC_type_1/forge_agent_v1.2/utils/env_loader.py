import os
from dotenv import load_dotenv

def load_env_variables():
    """
    Load environment variables from a .env file.
    Returns a dictionary with GitHub-related configuration.
    """
    load_dotenv()
    return {
        "github_url": os.getenv("GITHUB_URL"),
        "oauth_token": os.getenv("OAUTH_TOKEN"),
        "branch_name": os.getenv("BRANCH_NAME"),
        "main_branch_name": os.getenv("MAIN_BRANCH_NAME"),
    }
