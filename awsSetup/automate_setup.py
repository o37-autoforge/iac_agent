import os
import json
import shutil
import subprocess
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

def load_accounts(file_path):
    with open(file_path, 'r') as f:
        accounts = json.load(f)
    return accounts

def create_env_file(account, template_path, output_path):
    with open(template_path, 'r') as f:
        env_content = f.read()
    
    # Replace placeholders with account-specific values
    env_content = env_content.replace('ACCOUNT_ID=', f"ACCOUNT_ID={account['account_id']}")
    env_content = env_content.replace('AWS_REGION=', f"AWS_REGION={account['aws_region']}")
    env_content = env_content.replace('AWS_ACCESS_KEY_ID=', f"AWS_ACCESS_KEY_ID={account['aws_access_key_id']}")
    env_content = env_content.replace('AWS_SECRET_ACCESS_KEY=', f"AWS_SECRET_ACCESS_KEY={account['aws_secret_access_key']}")
    env_content = env_content.replace('S3_BUCKET_NAME=', f"S3_BUCKET_NAME={account['s3_bucket_name']}")
    env_content = env_content.replace('RDS_DB_IDENTIFIER=', f"RDS_DB_IDENTIFIER={account['rds_db_identifier']}")
    env_content = env_content.replace('LAMBDA_FUNCTION_NAME=', f"LAMBDA_FUNCTION_NAME={account['lambda_function_name']}")
    env_content = env_content.replace('LAMBDA_ROLE_NAME=', f"LAMBDA_ROLE_NAME={account['lambda_role_name']}")
    env_content = env_content.replace('CONFIG_ROLE_NAME=', f"CONFIG_ROLE_NAME={account['config_role_name']}")
    env_content = env_content.replace('RDS_DB_USERNAME=', f"RDS_DB_USERNAME={account['rds_db_username']}")
    env_content = env_content.replace('RDS_DB_PASSWORD=', f"RDS_DB_PASSWORD={account['rds_db_password']}")
    env_content = env_content.replace('SECRET_NAME=AWSConfigDBCredentials', f"SECRET_NAME={account.get('SECRET_NAME', 'AWSConfigDBCredentials')}")

    if account.get('vpc_subnet_ids'):
        env_content = env_content.replace('VPC_SUBNET_IDS=', f"VPC_SUBNET_IDS={account['vpc_subnet_ids']}")
    if account.get('vpc_security_group_ids'):
        env_content = env_content.replace('VPC_SECURITY_GROUP_IDS=', f"VPC_SECURITY_GROUP_IDS={account['vpc_security_group_ids']}")

    with open(output_path, 'w') as f:
        f.write(env_content)
    print(f"Created .env file for account {account['account_id']} at {output_path}")


def copy_required_files(account_dir):
    required_files = ['.env.template', 'lambda_function.py', 'package_lambda.sh', 'setup_aws_config_database.py', 'database_schema.sql']
    for file in required_files:
        source = Path(__file__).parent / file  # Use the directory of the current script
        destination = account_dir / file
        if source.exists():
            shutil.copy(source, destination)
            print(f"Copied {file} to {account_dir}")
        else:
            print(f"Error: {file} does not exist in the source directory.")

               
import os
import subprocess
from pathlib import Path

def package_lambda(setup_dir):
    # Construct the path to the package_lambda.sh script
    script_path = os.getcwd() / setup_dir / 'package_lambda.sh'
    
    # Convert to absolute path
    script_path = script_path.resolve()

    # Print the setup directory and script path for debugging
    print(f"Setup directory: {setup_dir}")
    print(f"Script path: {script_path}")

    # Check if the script exists at the specified path
    if not script_path.exists():
        print(f"Error: {script_path} does not exist.")
        return

    # Ensure the package_lambda.sh script is executable
    os.chmod(script_path, 0o755)
    print(f"Set executable permissions for {script_path}")

    # Execute the packaging script
    try:
        # Print the current working directory before running the subprocess
        print(f"Current working directory before subprocess: {os.getcwd()}")
        
        # Run the script with the correct working directory
        subprocess.run([str(script_path)], cwd=str(setup_dir.resolve()), check=True)
        print("Lambda function packaged successfully.")
    except subprocess.CalledProcessError as e:
        print(f"Error executing {script_path}: {e}")
    except FileNotFoundError as e:
        print(f"File not found error: {e}")

def run_setup_script(setup_dir):
    # Execute the setup_aws_config_database.py script
    subprocess.run(['python', 'setup_aws_config_database.py'], cwd=setup_dir, check=True)
    print("Setup script executed successfully.")

def automate_account_setup(account):
    try:
        # Create a unique directory for each account
        account_dir = Path(f"setup_account_{account['account_id']}")
        account_dir.mkdir(exist_ok=True)
        
        # Copy required files into the account directory
        copy_required_files(account_dir)

        # Create the .env file
        create_env_file(
            account=account,
            template_path=account_dir / '.env.template',
            output_path=account_dir / '.env'
        )

        # Package the Lambda function
        package_lambda(account_dir)

        # Run the setup script
        run_setup_script(account_dir)

        print(f"Setup completed for account {account['account_id']}\n")
    except subprocess.CalledProcessError as e:
        print(f"Setup failed for account {account['account_id']}: {e}\n")
    except Exception as e:
        print(f"An error occurred for account {account['account_id']}: {e}\n")

def main():
    accounts_file = './accounts.json'
    accounts = load_accounts(accounts_file)

    MAX_WORKERS = 5  # Adjust based on our VM's capabilities

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_to_account = {executor.submit(automate_account_setup, account): account for account in accounts}
        for future in as_completed(future_to_account):
            account = future_to_account[future]
            try:
                future.result()
            except Exception as e:
                print(f"Setup encountered an error for account {account['account_id']}: {e}")

if __name__ == "__main__":
    main()
