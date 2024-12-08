import os
from pathlib import Path
from dotenv import load_dotenv
from validation_agent import start_validation_agent
from utils.forge_interface import ForgeInterface
from utils.subprocess_handler import SubprocessHandler
from utils.rag_utils import RAGUtils
import asyncio

def test_validation():
    # Load environment variables
    load_dotenv()
    
    if not os.getenv("OPENAI_API_KEY"):
        raise ValueError("OPENAI_API_KEY not found in environment variables")
    
    # Get repo path from environment and convert to Path
    repo_path = Path(os.getenv("REPO_PATH"))
    if not repo_path:
        raise ValueError("REPO_PATH not found in environment variables")
    
    # Initialize forge with repo path
    subprocess_handler = SubprocessHandler(repo_path)
    subprocess_handler.start_forge(os.getenv("OPENAI_API_KEY"), [])
    forge_interface = ForgeInterface(subprocess_handler)
    
    # Initialize RAG utils
    rag_utils = RAGUtils(repo_path)
    
    # Add files to forge context
    async def add_files_to_forge():
        files_to_add = ['main.tf', 'variables.tf']
        for file_path in files_to_add:
            try:
                print(f"Adding {file_path} to forge context...")
                result = await forge_interface.add_file_to_context(file_path)
                if result:
                    print(f"Successfully added {file_path} to forge context")
                else:
                    print(f"Failed to add {file_path} to forge context")
            except Exception as e:
                print(f"Error adding {file_path} to forge context: {str(e)}")
    
    # Run the async function
    loop = asyncio.get_event_loop()
    try:
        loop.run_until_complete(add_files_to_forge())
    except Exception as e:
        print(f"Error setting up forge context: {str(e)}")
    finally:
        loop.close()
    
    implementation_plan = """

    1. **Original User Query**
    - Add an S3 bucketwwwwwwww

    2. **Code Changes Needed**

    **File: main.tf**
    - Add the following Terraform resource block to define the S3 bucket with the specified configurations:
        ```hcl
        resource "aws_s3_bucket" "project_environment_bucket" {
          bucket = "project-environment-bucket"
          acl    = "private"

          versioning {
            enabled = true
          }

          server_side_encryption_configuration {
            rule {
              apply_server_side_encryption_by_default {
                sse_algorithm = "AES256"
              }
            }
          }
        }
        
        resource "aws_s3_bucket_policy" "project_environment_bucket_policy" {
          bucket = aws_s3_bucket.project_environment_bucket.id

          policy = jsonencode({
            Version = "2012-10-17",
            Statement = [
              {
                Effect   = "Allow",
                Principal = {
                  AWS = "arn:aws:iam::account-id:root"
                },
                Action   = "s3:*",
                Resource = [
                  "${aws_s3_bucket.project_environment_bucket.arn}/*",
                  "${aws_s3_bucket.project_environment_bucket.arn}"
                ]
              }
            ]
          })
        }

    """
    
    try:
        # Start validation agent with forge interface and rag utils
        final_state = start_validation_agent(
            implementation_plan=implementation_plan,
            repo_path=repo_path,
            forge_interface=forge_interface,
            rag_utils=rag_utils  # Add RAG utils
        )
        
        # Print results
        print("\nValidation Results:")
        print("=" * 80)
        print(f"Status: {final_state['validation_status']}")
        
        if final_state.get('detected_issues'):
            print("\nDetected Issues:")
            for issue in final_state['detected_issues']:
                print(f"- {issue}")
        
        if 'fix_query' in final_state.get('memory', {}):
            print("\nFix Query:")
            print(final_state['memory']['fix_query'])
        
        # Print command history
        if 'commands' in final_state.get('memory', {}):
            print("\nRemaining Commands:")
            for cmd in final_state['memory']['commands']:
                print(f"\nCommand: {cmd['command']}")
                print(f"Purpose: {cmd['purpose']}")
                print(f"Expected Output: {cmd['expected_output']}")
        
        # Print execution history
        if 'executed_commands' in final_state.get('memory', {}):
            print("\nExecuted Commands:")
            for cmd in final_state['memory']['executed_commands']:
                print(f"\nCommand: {cmd['command']}")
                print(f"Output: {cmd.get('output', 'No output recorded')}")
                print(f"Status: {cmd.get('status', 'Unknown')}")
        
    finally:
        # Cleanup
        subprocess_handler.close_forge()

if __name__ == "__main__":
    test_validation() 