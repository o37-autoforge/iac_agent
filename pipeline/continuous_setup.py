import time
import os
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
import json
import logging
import git
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
import google.generativeai as genai
import asyncio
from datetime import datetime


# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.ERROR,
    format='%(asctime)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# Initialize LLMs
llm = ChatOpenAI(model="gpt-4o", temperature=0)
genai.configure(api_key=os.getenv("GOOGLE_API_KEY"))
gemini_llm = genai.GenerativeModel(model_name="gemini-1.5-pro")

def clone_repository():
    """Clone the repository specified in GITHUB_REPO_URL"""
    # Load environment variables
    load_dotenv()
    
    # Get and verify environment variables
    repo_url = os.getenv('REPO_URL')
    repo_branch = os.getenv('BRANCH_NAME', 'main')
    repo_path = os.getenv('REPO_PATH')
    github_token = os.getenv('GITHUB_TOKEN')
    
    print("\nChecking repository configuration:")
    print(f"REPO_URL: {repo_url}")
    print(f"BRANCH_NAME: {repo_branch}")
    print(f"REPO_PATH: {repo_path}")
    print(f"GITHUB_TOKEN: {'Set' if github_token else 'Not Set'}")
    
    if not repo_url:
        raise ValueError("REPO_URL not found in .env file")
    if not repo_path:
        raise ValueError("REPO_PATH not found in .env file")
    if not github_token:
        raise ValueError("GITHUB_TOKEN not found in .env file")
    
    if os.path.exists(repo_path):
        print(f"\nRemoving existing repo directory: {repo_path}")
        import shutil
        shutil.rmtree(repo_path)
    
    # Create parent directories if they don't exist
    os.makedirs(os.path.dirname(repo_path), exist_ok=True)
    
    print(f"\nCloning repository from {repo_url} to {repo_path}")
    
    try:
        # Construct authenticated URL
        auth_url = repo_url.replace('https://', f'https://{github_token}@')
        
        # Clone repository
        repo = git.Repo.clone_from(auth_url, repo_path)
        repo.git.checkout(repo_branch)
        print("Repository cloned successfully!")
        
        return repo_path
        
    except git.GitCommandError as e:
        print(f"Error cloning repository: {str(e)}")
        raise
    except Exception as e:
        print(f"Unexpected error during cloning: {str(e)}")
        raise

class CodeAnalyzer:
    def __init__(self, llm, gemini_llm):
        self.llm = llm
        self.gemini_llm = gemini_llm
        
        # Directories and patterns to exclude
        self.excluded_patterns = [
            '.git',
            'analysis',
            'forge',
            '.forge',
            'forge_IAC_type',
            'forge_agent',
            'setupTools',
            'pipeline',
            '__pycache__',
            '.pytest_cache',
            'agent_states',
            '.analysis',
            'implementation_plan.txt',
            'file_tree.txt',
            'codebase_overview.txt',
            'website',
            'cloned_repo'
        ]
        
        # IaC-specific file extensions
        self.iac_extensions = {
            # Terraform and HCL
            '.tf': 'terraform',
            '.tfvars': 'terraform-vars',
            '.hcl': 'hcl',
            
            # Cloud Formation and AWS
            '.template': 'cloudformation',
            '.yaml': 'yaml',
            '.yml': 'yaml',
            
            # Kubernetes and Container
            'dockerfile': 'docker',
            'docker-compose.yml': 'docker-compose',
            'docker-compose.yaml': 'docker-compose',
            
            # Configuration and Variables
            '.env': 'env-vars',
            '.conf': 'config',
            '.json': 'json',
            '.toml': 'toml',
            
            # Documentation
            'readme.md': 'documentation',
            '.md': 'documentation'
        }

    def _should_exclude_path(self, path):
        """Check if a path should be excluded from analysis"""
        path_lower = path.lower()
        # Check if path contains any excluded pattern
        return any(pattern.lower() in path_lower for pattern in self.excluded_patterns)

    def analyze_file(self, file_path):
        """Generate detailed analysis for a single file"""
        try:
            # Skip if file should be excluded
            if self._should_exclude_path(file_path):
                return None

            # Check if file is IaC-relevant
            file_name = os.path.basename(file_path).lower()
            file_ext = os.path.splitext(file_name)[1].lower()
            
            # Check both exact filename and extension
            if file_name not in self.iac_extensions and file_ext not in self.iac_extensions:
                return None

            with open(file_path, 'r') as f:
                content = f.read()

            if not content.strip():
                return None

            # Get file type for specialized prompts
            file_type = self.iac_extensions.get(file_name) or self.iac_extensions.get(file_ext)

            # Specialized prompts based on file type
            if file_type == 'terraform':
                prompt = f"""Analyze this Terraform file and explain its infrastructure configuration:
                - Resources being created/managed
                - Provider configuration
                - Variables and data sources
                - Key configurations and their purpose
                - Security considerations
                
                File: {file_name}
                Content:
                {content}
                """
            elif file_type in ['yaml', 'docker-compose']:
                prompt = f"""Analyze this YAML configuration file and explain:
                - Service/resource definitions
                - Key configurations
                - Dependencies and relationships
                - Environment settings
                
                File: {file_name}
                Content:
                {content}
                """
            elif file_type == 'docker':
                prompt = f"""Analyze this Dockerfile and explain:
                - Base image and build stages
                - Key commands and their purpose
                - Exposed ports and volumes
                - Runtime configurations
                
                File: {file_name}
                Content:
                {content}
                """
            elif file_type in ['env-vars', 'config']:
                prompt = f"""Analyze this configuration file and explain:
                - Key variables/settings
                - Purpose of configurations
                - Environment-specific settings
                
                File: {file_name}
                Content:
                {content}
                """
            else:
                prompt = f"""Analyze this infrastructure file and explain its purpose and configuration:
                - Key elements and their purpose
                - Important settings
                - Integration points
                
                File: {file_name}
                Content:
                {content}
                """

            return self.llm.invoke(prompt).content

        except Exception as e:
            logger.error(f"Error analyzing file {file_path}: {str(e)}")
            return None

    def analyze_codebase(self, root_path):
        """Generate comprehensive analysis of the entire codebase using Gemini"""
        try:
            file_tree = self._generate_file_tree(root_path)
            
            all_files = []
            for root, _, files in os.walk(root_path):
                # Skip excluded directories
                if self._should_exclude_path(root):
                    continue
                
                for file in files:
                    # Skip excluded files
                    if self._should_exclude_path(file):
                        continue
                        
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
            # Skip excluded directories
            dirs[:] = [d for d in dirs if not self._should_exclude_path(d)]
            
            if self._should_exclude_path(root):
                continue
                
            level = root.replace(directory, '').count(os.sep)
            indent = ' ' * 4 * level
            subdir = os.path.basename(root)
            tree_lines.append(f"{indent}{subdir}/")
            subindent = ' ' * 4 * (level + 1)
            for f in sorted(files):
                if not self._should_exclude_path(f):
                    tree_lines.append(f"{subindent}{f}")
        return '\n'.join(tree_lines)

class CodebaseOverviewHandler(FileSystemEventHandler):
    def __init__(self, repo_path: str, llm=llm, gemini_llm=gemini_llm):
        self.repo_path = repo_path
        self.analysis_dir = os.path.join(repo_path, 'analysis')
        self.llm = llm
        self.gemini_llm = gemini_llm
        self.analyzer = CodeAnalyzer(self.llm, self.gemini_llm)
        
        # Ensure analysis directory exists
        os.makedirs(self.analysis_dir, exist_ok=True)
        print(f"Initialized analysis directory at: {self.analysis_dir}")
        
        # Initial analysis of all files
        print("\nStarting initial analysis of codebase...")
        self.update_all_overviews()

    def update_file_overview(self, file_path):
        """Generate and save overview for a specific file"""
        if not self.is_repo_file(file_path):
            return

        try:
            analysis = self.analyzer.analyze_file(file_path)
            if analysis:
                relative_path = os.path.relpath(file_path, self.repo_path)
                analysis_file = os.path.join(self.analysis_dir, f"{relative_path}.analysis")
                
                # Create subdirectories if needed
                os.makedirs(os.path.dirname(analysis_file), exist_ok=True)
                
                with open(analysis_file, 'w') as f:
                    f.write(analysis)
                print(f"Updated analysis for: {relative_path}")
                
        except Exception as e:
            logger.error(f"Error analyzing file {file_path}: {str(e)}")

    def update_codebase_overview(self):
        """Generate and save a comprehensive overview of the entire codebase"""
        try:
            # Generate file tree first
            tree = []
            for root, dirs, files in os.walk(self.repo_path):
                # Skip excluded directories
                dirs[:] = [d for d in dirs if not self.analyzer._should_exclude_path(d)]
                
                level = root.replace(self.repo_path, '').count(os.sep)
                indent = '  ' * level
                tree.append(f"{indent}{os.path.basename(root)}/")
                
                for file in files:
                    if not self.analyzer._should_exclude_path(file):
                        tree.append(f"{indent}  {file}")
            
            # Save the file tree
            tree_path = os.path.join(self.analysis_dir, 'file_tree.txt')
            with open(tree_path, 'w') as f:
                f.write('\n'.join(tree))
            print("Updated file tree")
            
            # Generate codebase overview using file tree and existing analyses
            file_descriptions = self.get_file_descriptions()
            
            part1 = '\n'.join(tree)
            # Create overview prompt without f-string for newlines
            overview_prompt = f"""
            
                As an Infrastructure as Code expert, provide a comprehensive overview of this codebase."
                
                "File Tree:"
                {part1}
               
                "File Descriptions:"
                {json.dumps(file_descriptions, indent=2)}

                "Focus on:"
                "1. Overall architecture and design patterns"
                "2. Key infrastructure components and their relationships"
                "3. Resource management and organization"
                "4. Security configurations and compliance measures"
                "5. Integration points and dependencies"
                "6. Notable patterns or potential concerns"
                "Provide a clear, organized summary that helps understand the infrastructure design."
            
            """
            
            overview = self.llm.invoke(overview_prompt).content
            
            # Save the overview
            overview_path = os.path.join(self.analysis_dir, 'codebase_overview.txt')
            with open(overview_path, 'w') as f:
                f.write(overview)
            print("Updated codebase overview")
        except Exception as e:
            logger.error(f"Error updating codebase overview: {str(e)}")
            raise

    def update_all_overviews(self):
        """Update all file and codebase overviews"""
        print("Starting initial analysis of all files...")
        
        # Update individual file overviews first
        for root, _, files in os.walk(self.repo_path):
            if self.analyzer._should_exclude_path(root):
                continue
            for file in files:
                if not self.analyzer._should_exclude_path(file):
                    file_path = os.path.join(root, file)
                    self.update_file_overview(file_path)
        
        # Update codebase overview after all files are analyzed
        print("Generating codebase overview...")
        self.update_codebase_overview()
        print("Completed initial analysis")

    def is_repo_file(self, file_path):
        """Check if a file is a repository file (not in excluded directories)"""
        rel_path = os.path.relpath(file_path, self.repo_path)
        return not self.analyzer._should_exclude_path(rel_path)

    def on_modified(self, event):
        if not event.is_directory and self.is_repo_file(event.src_path):
            print(f"\nFile modified: {os.path.relpath(event.src_path, self.repo_path)}")
            self.update_file_overview(event.src_path)
            self.update_codebase_overview()

    def on_created(self, event):
        if not event.is_directory and self.is_repo_file(event.src_path):
            print(f"\nNew file created: {os.path.relpath(event.src_path, self.repo_path)}")
            self.update_file_overview(event.src_path)
            self.update_codebase_overview()

    def on_deleted(self, event):
        if not event.is_directory and self.is_repo_file(event.src_path):
            print(f"\nFile deleted: {os.path.relpath(event.src_path, self.repo_path)}")
            relative_path = os.path.relpath(event.src_path, self.repo_path)
            analysis_file = os.path.join(self.analysis_dir, f"{relative_path}.analysis")
            if os.path.exists(analysis_file):
                os.remove(analysis_file)
            self.update_codebase_overview()

    def get_file_tree(self) -> str:
        """Get the file tree"""
        tree_path = os.path.join(self.analysis_dir, 'file_tree.txt')
        if os.path.exists(tree_path):
            with open(tree_path, 'r') as f:
                return f.read()
        return ""

    def get_file_descriptions(self) -> dict:
        """Get all file descriptions from analysis files"""
        descriptions = {}
        for root, _, files in os.walk(self.analysis_dir):
            for file in files:
                if file.endswith('.analysis'):
                    relative_path = os.path.relpath(
                        os.path.join(root, file),
                        self.analysis_dir
                    ).replace('.analysis', '')
                    with open(os.path.join(root, file), 'r') as f:
                        descriptions[relative_path] = f.read()
        return descriptions

    def get_codebase_overview(self) -> str:
        """Get the codebase overview"""
        overview_path = os.path.join(self.analysis_dir, 'codebase_overview.txt')
        if os.path.exists(overview_path):
            with open(overview_path, 'r') as f:
                return f.read()
        return ""

def start_continuous_setup(repo_path: str) -> dict:
    """Start continuous setup process"""
    try:
        # Clone repository if it doesn't exist
        if not os.path.exists(repo_path):
            print("\nCloning repository...")
            repo_path = clone_repository()
            print(f"Repository cloned to: {repo_path}")
        
        # Create analysis directory
        analysis_dir = os.path.join(repo_path, 'analysis')
        os.makedirs(analysis_dir, exist_ok=True)
        print(f"\nCreated analysis directory at: {analysis_dir}")
        
        # Initialize file watcher with LLM instances
        event_handler = CodebaseOverviewHandler(repo_path, llm=llm, gemini_llm=gemini_llm)
        observer = Observer()
        observer.schedule(event_handler, repo_path, recursive=True)
        observer.start()
        
        # Wait for initial analysis to complete
        while not os.path.exists(os.path.join(analysis_dir, 'codebase_overview.txt')):
            print("Analyzing codebase...")
            time.sleep(2)
        
        print("\nInitial analysis complete!")
        print("Now monitoring for file changes...")
        
        # Return state with analysis results
        return {
            'observer': observer,
            'codebase_overview': event_handler.get_codebase_overview(),
            'file_tree': event_handler.get_file_tree(),
            'file_descriptions': event_handler.get_file_descriptions(),
            'analysis_dir': analysis_dir
        }
        
    except Exception as e:
        logger.error(f"Error during continuous setup: {str(e)}")
        raise

if __name__ == "__main__":
    setup_state = start_continuous_setup()
    try:
        while True:
            time.sleep(1)  # Check for file changes every second
    except KeyboardInterrupt:
        setup_state['observer'].stop()
        logging.info("Stopping file monitoring")
    
    setup_state['observer'].join() 