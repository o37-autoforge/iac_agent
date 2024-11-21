# forge_agent/aws_handler.py

import os
import sys
import logging
from pathlib import Path
from dotenv import load_dotenv, set_key
from getpass import getpass
import boto3
from botocore.exceptions import NoCredentialsError, PartialCredentialsError, ClientError

logger = logging.getLogger(__name__)
def disable_logging():
    original_log_handlers = logging.getLogger().handlers[:]
    for handler in original_log_handlers:
        logging.getLogger().removeHandler(handler)

disable_logging()

class AWSHandler:
    def __init__(self):
        load_dotenv()
        self.ensure_aws_credentials()

    def ensure_aws_credentials(self):
        aws_access_key = os.getenv("AWS_ACCESS_KEY_ID")
        aws_secret_key = os.getenv("AWS_SECRET_ACCESS_KEY")
        aws_region = os.getenv("AWS_DEFAULT_REGION")

        missing_credentials = []
        if not aws_access_key:
            missing_credentials.append("AWS_ACCESS_KEY_ID")
        if not aws_secret_key:
            missing_credentials.append("AWS_SECRET_ACCESS_KEY")
        if not aws_region:
            missing_credentials.append("AWS_DEFAULT_REGION")

        if not missing_credentials:
            logger.info("All AWS credentials are already configured.")
            return

        print("\nAWS credentials are not fully configured. Please provide the missing credentials.")
        logger.info("Prompting user to input missing AWS credentials.")

        # Prompt user for missing credentials
        aws_credentials = {}
        if "AWS_ACCESS_KEY_ID" in missing_credentials:
            aws_credentials["AWS_ACCESS_KEY_ID"] = input("Enter your AWS Access Key ID: ").strip()
        if "AWS_SECRET_ACCESS_KEY" in missing_credentials:
            aws_credentials["AWS_SECRET_ACCESS_KEY"] = getpass("Enter your AWS Secret Access Key: ").strip()
        if "AWS_DEFAULT_REGION" in missing_credentials:
            aws_credentials["AWS_DEFAULT_REGION"] = input("Enter your AWS Default Region (e.g., us-east-1): ").strip()

        # Validate the provided credentials
        if not self.validate_aws_credentials(aws_credentials):
            logger.error("Invalid AWS credentials provided.")
            print("The AWS credentials provided are invalid. Please check and try again.")
            sys.exit(1)

        # Save the credentials to the .env file
        self.save_aws_credentials_to_env(aws_credentials)
        # print("AWS credentials have been configured successfully.\n")
        logger.info("AWS credentials have been configured and saved to .env file.")

    def validate_aws_credentials(self, credentials) -> bool:
        try:
            session = boto3.Session(
                aws_access_key_id=credentials.get("AWS_ACCESS_KEY_ID"),
                aws_secret_access_key=credentials.get("AWS_SECRET_ACCESS_KEY"),
                region_name=credentials.get("AWS_DEFAULT_REGION")
            )
            sts = session.client('sts')
            sts.get_caller_identity()
            logger.info("AWS credentials validated successfully.")
            return True
        except (NoCredentialsError, PartialCredentialsError, ClientError) as e:
            logger.error(f"AWS credentials validation failed: {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error during AWS credentials validation: {e}")
            return False

    def save_aws_credentials_to_env(self, credentials):
        env_path = Path('.env')
        for key, value in credentials.items():
            set_key(str(env_path), key, value)
        load_dotenv()
