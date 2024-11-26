import os
from dotenv import load_dotenv

def load_env_variables():
    """
    Load environment variables from a .env file.
    Returns a dictionary with GitHub-related configuration.
    """
    load_dotenv()
    return {
        "REPO_URL": os.getenv("REPO_URL"),
        "GITHUB_TOKEN": os.getenv("GITHUB_TOKEN"),
        "BRANCH_NAME": os.getenv("BRANCH_NAME"),
        "MAIN_BRANCH": os.getenv("MAIN_BRANCH"),
        "REPO_PATH": os.getenv("REPO_PATH"),
        
    }
