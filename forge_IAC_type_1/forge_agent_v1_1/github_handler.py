from pathlib import Path
import logging
import git
from dotenv import load_dotenv
import os
import shutil
from github import Github  # Import PyGithub

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
def disable_logging():
    original_log_handlers = logging.getLogger().handlers[:]
    for handler in original_log_handlers:
        logging.getLogger().removeHandler(handler)

disable_logging()

class GitHandler:
    def __init__(self):
        # Load environment variables
        load_dotenv()

        # Get required environment variables
        self.github_token = os.getenv('GITHUB_TOKEN')
        self.repo_url = os.getenv('REPO_URL')
        self.repo_path = Path(os.getenv('REPO_PATH'))
        self.branch_name = os.getenv('BRANCH_NAME')  # This is the branch you will push to
        self.main_branch = os.getenv('MAIN_BRANCH')  # The base branch for the PR

        # Validate environment variables
        if not all([self.github_token, self.repo_url, self.repo_path, self.branch_name, self.main_branch]):
            raise EnvironmentError("Missing required environment variables")

    def clone_repository(self):
        """Clone the repository to the specified local path"""
        try:
            # Remove existing repository if it exists
            if self.repo_path.exists():
                logger.info(f"Removing existing repository at {self.repo_path}")
                shutil.rmtree(self.repo_path)

            # Create parent directories if they don't exist
            self.repo_path.parent.mkdir(parents=True, exist_ok=True)

            # Construct authenticated URL
            auth_url = self.repo_url.replace('https://', f'https://{self.github_token}@')

            # Clone repository
            logger.info(f"Cloning repository to {self.repo_path}")
            repo = git.Repo.clone_from(
                auth_url,
                self.repo_path,
                branch=self.main_branch  # Clone the main branch
            )

            logger.info(f"Successfully cloned repository to {self.repo_path}")
            return repo

        except git.GitCommandError as e:
            logger.error(f"Failed to clone repository: {str(e)}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error: {str(e)}")
            raise

    def create_pull_request(self, title, body):
        """
        Create a pull request on GitHub from the feature branch to the main branch.

        :param title: Title of the pull request.
        :param body: Body/description of the pull request.
        """
        try:
            # Authenticate with GitHub
            g = Github(self.github_token)

            # Extract repository owner and name from the URL
            path_parts = self.repo_url.rstrip('/').split('/')
            repo_name = path_parts[-1].replace('.git', '')  # Remove '.git' if present
            owner_name = path_parts[-2]



            # Access the repository
            github_repo = g.get_repo(f"{owner_name}/{repo_name}")

            # Get the authenticated user
            user = g.get_user()

            # Find existing pull requests from your branch
            pulls = github_repo.get_pulls(state='open', head=self.branch_name, base=self.main_branch)

            # Close existing pull requests
            for pr in pulls:
                print(f"Closing PR #{pr.number}")
                pr.edit(state='closed')

            # Create the pull request
            pr = github_repo.create_pull(
                title=title,
                body=body,
                head=self.branch_name,
                base=self.main_branch
            )

            logger.info(f"Successfully created pull request: {pr.html_url}")
            return pr

        except Exception as e:
            logger.error(f"Failed to create pull request: {str(e)}")
            raise
