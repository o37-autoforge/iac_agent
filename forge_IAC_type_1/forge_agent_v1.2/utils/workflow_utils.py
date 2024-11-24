from concurrent.futures import ThreadPoolExecutor, as_completed
import boto3
import os
import json


def execute_parallel_tasks(tasks, state):
    """
    Execute multiple tasks in parallel using threads.

    Args:
        tasks (list of callables): Functions to execute in parallel.
        state (dict): Shared state object.

    Returns:
        list: Results of the executed tasks.
    """
    results = []
    with ThreadPoolExecutor() as executor:
        futures = {executor.submit(task, state): task.__name__ for task in tasks}
        for future in as_completed(futures):
            try:
                result = future.result()
                results.append(result)
            except Exception as e:
                print(f"Error in {futures[future]}: {e}")
    return results


def configure_aws_from_env():
#    return {"identity": "aws-identity-example"}

    """
    Read AWS root credentials from environment variables and configure AWS CLI.

    Environment Variables:
        AWS_ACCESS_KEY_ID: Your AWS Access Key ID.
        AWS_SECRET_ACCESS_KEY: Your AWS Secret Access Key.
        AWS_DEFAULT_REGION: Your preferred AWS region.

    Returns:
        dict: A dictionary confirming the configuration.
    """
    aws_access_key_id = os.getenv("AWS_ACCESS_KEY_ID")
    aws_secret_access_key = os.getenv("AWS_SECRET_ACCESS_KEY")
    aws_default_region = os.getenv("AWS_DEFAULT_REGION", "us-east-1")

    if not aws_access_key_id or not aws_secret_access_key:
        raise ValueError("AWS credentials are not set in environment variables.")

    print("Configuring AWS...")
    boto3.setup_default_session(
        aws_access_key_id=aws_access_key_id,
        aws_secret_access_key=aws_secret_access_key,
        region_name=aws_default_region
    )

    # Optionally, verify configuration by calling STS GetCallerIdentity
    client = boto3.client("sts")
    identity = client.get_caller_identity()
    print(f"Configured AWS with identity: {identity['Arn']}")

    return {
        "status": "AWS configured",
        "region": aws_default_region,
        "identity": identity["Arn"],
    }
