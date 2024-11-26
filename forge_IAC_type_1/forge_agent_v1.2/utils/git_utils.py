import os
import git
from pathlib import Path
from .env_loader import load_env_variables

from dotenv import load_dotenv

load_dotenv()

def clone_repository(state):
    """
    Clone or update a repository from a given URL to a specified path.
    """
    repo_url = os.environ.get("REPO_URL")
    repo_path = os.environ.get("REPO_PATH")
    branch_name = os.environ.get("BRANCH_NAME")
    print(f"Repo path: {repo_path}")
    if os.path.exists(repo_path):
        try:
            # If the repository already exists, perform a pull
            repo = git.Repo(repo_path)
            origin = repo.remotes.origin
            origin.pull(branch_name)
        except Exception as e:
            raise RuntimeError(f"Failed to pull latest changes: {e}")
    else:
        try:
            # If the repository does not exist, clone it
            repo = git.Repo.clone_from(repo_url, repo_path, branch=branch_name)
        except git.exc.GitCommandError as e:
            raise RuntimeError(f"Failed to clone repository: {e}")

    state["repo_path"] = repo_path

    return state

def create_combined_txt(state):
    """
    Load all files from the cloned repository and write their content into a single text file.

    Args:
        repo_path (str): The local path of the cloned repository.
        output_dir (str): The directory where the combined `.txt` file will be saved.
    """
    rag_path = os.environ.get("RAG_DATABASE_PATH")
    repo_path = os.environ.get("REPO_PATH")

    output_dir: str = rag_path
    output_path = Path(output_dir) / "cb_combined.txt"
    state["combined_file_path"] = output_path
    combined_content = []
    repo_path = Path(repo_path)

    # Ensure the output directory exists
    os.makedirs(output_dir, exist_ok=True)

    for root, _, files in os.walk(repo_path):
        for file in files:
            file_path = Path(root) / file
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    content = f.read()
                    combined_content.append(f"File: {file_path.relative_to(repo_path)}\nContent:\n{content}\n{'='*40}\n")
            except Exception as e:
                print(f"Skipping file {file_path} due to error: {e}")

    with open(output_path, "w", encoding="utf-8") as output_file:
        output_file.writelines(combined_content)

    return state