import time
import os
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
import json
from pathlib import Path
import logging
import git
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
import google.generativeai as genai
import asyncio
from datetime import datetime
from utils.forge_interface import ForgeInterface
from utils.subprocess_handler import SubprocessHandler

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

# Initialize LLMs
llm = ChatOpenAI(model="gpt-4o", temperature=0)
genai.configure(api_key=os.getenv("GOOGLE_API_KEY"))
gemini_llm = genai.GenerativeModel(model_name="gemini-1.5-pro")

def clone_repository():
    """Clone the repository specified in GITHUB_REPO_URL"""
    repo_url = os.getenv('REPO_URL')
    repo_branch = os.getenv('BRANCH_NAME', 'main')
    
    if not repo_url:
        raise ValueError("GITHUB_REPO_URL not found in .env file")
    
    # Create a 'repo' directory in the current working directory
    repo_path = os.path.join(os.getcwd(), 'repo')
    
    if os.path.exists(repo_path):
        logging.info("Removing existing repo directory")
        import shutil
        shutil.rmtree(repo_path)
    
    logging.info(f"Cloning repository from {repo_url}")
    repo = git.Repo.clone_from(repo_url, repo_path)
    repo.git.checkout(repo_branch)
    
    return repo_path

class CodeAnalyzer:
    def __init__(self, llm, gemini_llm):
        self.llm = llm
        self.gemini_llm = gemini_llm

    def analyze_file(self, file_path):
        """Generate detailed analysis for a single file"""
        try:
            with open(file_path, 'r') as f:
                content = f.read()

            prompt = f"""As an Infrastructure as Code expert, analyze this file and provide a detailed explanation.
            Focus on:
            1. Primary purpose and functionality
            2. Resource definitions and configurations
            3. Dependencies and integrations
            4. Variables, inputs, and outputs
            5. Security configurations and best practices
            6. Integration points with other infrastructure components
            7. Potential risks or considerations

            For Terraform files (.tf), also include:
            - Provider configurations
            - Resource naming patterns
            - State management implications
            - Module usage and structure

            For configuration files (.yaml, .json), also include:
            - Configuration hierarchy
            - Environment-specific settings
            - Service configurations
            - Default values and overrides

            Code content:
            {content}
            """

            analysis = self.llm.invoke(prompt).content
            return analysis
        except Exception as e:
            logging.error(f"Error analyzing file {file_path}: {str(e)}")
            return None

    def analyze_codebase(self, root_path):
        """Generate comprehensive analysis of the entire codebase using Gemini"""
        try:
            file_tree = self._generate_file_tree(root_path)
            
            all_files = []
            for root, _, files in os.walk(root_path):
                if '.git' in root or 'analysis' in root:
                    continue
                for file in files:
                    if file.endswith(('.tf', '.json', '.yaml', '.yml')):
                        file_path = os.path.join(root, file)
                        with open(file_path, 'r') as f:
                            content = f.read()
                        all_files.append(f"File: {file_path}\n{content[:1000]}...")  # First 1000 chars

            prompt = f"""As an Infrastructure as Code expert, provide a comprehensive analysis of this infrastructure codebase.
            
            Focus on:
            1. Infrastructure Architecture
               - Overall design patterns
               - Resource organization
               - Network architecture
               - Security model
            
            2. Resource Management
               - Resource types and purposes
               - Naming conventions
               - Tagging strategies
               - State management
            
            3. Configuration Patterns
               - Variable usage
               - Environment management
               - Secret handling
               - Default configurations
            
            4. Dependencies and Integrations
               - Service dependencies
               - External integrations
               - Module dependencies
               - Provider requirements
            
            5. Operational Aspects
               - Deployment patterns
               - Backup strategies
               - Monitoring setup
               - Maintenance considerations
            
            6. Best Practices Analysis
               - Security compliance
               - Resource optimization
               - Code maintainability
               - Documentation quality
            
            File Tree:
            {file_tree}

            Infrastructure Code:
            {all_files}
            
            Provide specific examples from the code where relevant.
            """

            return self.gemini_llm.generate_content(prompt).text
        except Exception as e:
            logging.error(f"Error analyzing codebase: {str(e)}")
            return None

    def _generate_file_tree(self, directory):
        """Generate a file tree structure"""
        tree_lines = []
        for root, dirs, files in os.walk(directory):
            # Skip .git and analysis directories
            dirs[:] = [d for d in dirs if d not in ['.git', 'analysis']]
            
            level = root.replace(directory, '').count(os.sep)
            indent = ' ' * 4 * level
            subdir = os.path.basename(root)
            tree_lines.append(f"{indent}{subdir}/")
            subindent = ' ' * 4 * (level + 1)
            for f in sorted(files):
                tree_lines.append(f"{subindent}{f}")
        return '\n'.join(tree_lines)

class CodebaseOverviewHandler(FileSystemEventHandler):
    def __init__(self, repo_path, subprocess_handler, forge_interface):
        self.repo_path = repo_path
        self.analysis_dir = os.path.join(repo_path, 'analysis')
        self.analyzer = CodeAnalyzer(llm, gemini_llm)
        os.makedirs(self.analysis_dir, exist_ok=True)
        
        # Store subprocess handlers
        self.subprocess_handler = subprocess_handler
        self.forge_interface = forge_interface
        
        # Start forge process
        self.start_forge_process()
        
        # Initial analysis of all files
        self.update_all_overviews()

    def start_forge_process(self):
        """Start the forge process and add relevant files to context"""
        try:
            # Get all relevant files for initial context
            relevant_files = []
            for root, _, files in os.walk(self.repo_path):
                if '.git' in root or 'analysis' in root:
                    continue
                for file in files:
                    if file.endswith(('.tf', '.yaml', '.yml', '.json')):
                        file_path = os.path.relpath(os.path.join(root, file), self.repo_path)
                        relevant_files.append(file_path)
            
            # Start forge process with these files
            self.subprocess_handler.start_forge(
                os.getenv('OPENAI_API_KEY'),
                [] #relevant_files
            )
            logging.info("Forge process started successfully")
        except Exception as e:
            logging.error(f"Error starting forge process: {str(e)}")

    def update_file_overview(self, file_path):
        """Generate and save overview for a specific file"""
        # Only process repository files
        if not self.is_repo_file(file_path):
            return

        # Only process text files
        if not self._is_text_file(file_path):
            return

        try:
            analysis = self.analyzer.analyze_file(file_path)
            if analysis:
                # Save analysis as .txt file
                file_name = os.path.basename(file_path)
                overview_path = os.path.join(self.analysis_dir, f"{file_name}_analysis.txt")
                with open(overview_path, 'w') as f:
                    f.write(analysis)
                
                logging.info(f"Updated analysis for {file_path}")
        except Exception as e:
            logging.error(f"Error processing file {file_path}: {str(e)}")

    def update_codebase_overview(self):
        """Generate and save a comprehensive overview of the entire codebase"""
        analysis = self.analyzer.analyze_codebase(self.repo_path)
        if analysis:
            # Save the main overview as txt
            overview_path = os.path.join(self.analysis_dir, 'codebase_overview.txt')
            with open(overview_path, 'w') as f:
                f.write(analysis)
            
            # Save the file tree separately
            tree = self.analyzer._generate_file_tree(self.repo_path)
            tree_path = os.path.join(self.analysis_dir, 'file_tree.txt')
            with open(tree_path, 'w') as f:
                f.write(tree)
            
            logging.info("Updated codebase overview and file tree")

    def update_all_overviews(self):
        """Update both codebase and individual file overviews"""
        self.update_codebase_overview()
        for root, _, files in os.walk(self.repo_path):
            if '.git' in root or 'analysis' in root:
                continue
            for file in files:
                file_path = os.path.join(root, file)
                self.update_file_overview(file_path)

    def is_repo_file(self, file_path):
        """Check if a file is a repository file (not in .git or analysis)"""
        rel_path = os.path.relpath(file_path, self.repo_path)
        return not (rel_path.startswith('.git') or rel_path.startswith('analysis'))

    def on_modified(self, event):
        if not event.is_directory and self.is_repo_file(event.src_path):
            self.update_file_overview(event.src_path)
            self.update_codebase_overview()

    def on_created(self, event):
        if not event.is_directory and self.is_repo_file(event.src_path):
            self.update_file_overview(event.src_path)
            self.update_codebase_overview()

    def on_deleted(self, event):
        if not event.is_directory and self.is_repo_file(event.src_path):
            file_name = os.path.basename(event.src_path)
            analysis_path = os.path.join(self.analysis_dir, f"{file_name}_analysis.txt")
            if os.path.exists(analysis_path):
                os.remove(analysis_path)
            self.update_codebase_overview()

    def _is_text_file(self, file_path):
        """Check if a file is a text file based on extension"""
        text_extensions = {'.py', '.js', '.ts', '.json', '.yaml', '.yml', '.tf', '.md', '.txt', '.ini', '.cfg'}
        return os.path.splitext(file_path)[1].lower() in text_extensions

def run_continuous_setup():
    """Run the continuous setup and return the subprocess handlers"""
    # Load environment variables
    load_dotenv()
    
    if not os.getenv('GOOGLE_API_KEY'):
        raise ValueError("GOOGLE_API_KEY not found in .env file")
    
    if not os.getenv('OPENAI_API_KEY'):
        raise ValueError("OPENAI_API_KEY not found in .env file")

    # Clone/update repository
    repo_path = clone_repository()
    
    # Initialize subprocess handler and forge interface
    subprocess_handler = SubprocessHandler(Path(repo_path))
    forge_interface = ForgeInterface(subprocess_handler)
    
    # Initialize and start the file watcher
    event_handler = CodebaseOverviewHandler(repo_path, subprocess_handler, forge_interface)
    observer = Observer()
    observer.schedule(event_handler, repo_path, recursive=True)
    observer.start()
    
    logging.info(f"Started monitoring repository: {repo_path}")
    
    return subprocess_handler, forge_interface, observer

if __name__ == "__main__":
    subprocess_handler, forge_interface, observer = run_continuous_setup()
    try:
        while True:
            time.sleep(1)  # Check for file changes every second
    except KeyboardInterrupt:
        observer.stop()
        subprocess_handler.close_forge()
        logging.info("Stopping file monitoring")
    
    observer.join() 