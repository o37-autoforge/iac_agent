import os
import git
from pathlib import Path
from .env_loader import load_env_variables

def clone_repository(repo_path: str = "./cloned_repo"):
    """
    Clone the repository to the agent's local machine.

    Args:
        repo_path (str): The local path where the repository will be cloned.
    """

    env_vars = load_env_variables()
    repo_url = env_vars["github_link"]
    token = env_vars["oauth_token"]
    branch_name = env_vars["branch_name"]

    # Construct the authenticated URL
    if token:
        repo_url = repo_url.replace("https://", f"https://{token}@")

    # Clone the repository
    if not os.path.exists(repo_path):
        print(f"Cloning repository from {repo_url} to {repo_path}...")
        git.Repo.clone_from(repo_url, repo_path, branch=branch_name)
        print("Repository cloned successfully!")
    else:
        print(f"Repository already exists at {repo_path}. Skipping cloning.")

    print(f"Cloned repository to {repo_path}")
    return repo_path

def create_combined_txt(repo_path: str, output_dir: str = "./rag/database"):
    """
    Load all files from the cloned repository and write their content into a single text file.

    Args:
        repo_path (str): The local path of the cloned repository.
        output_dir (str): The directory where the combined `.txt` file will be saved.
    """
    output_path = Path(output_dir) / "cb_combined.txt"
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

    print(f"Combined file created at {output_path}")
