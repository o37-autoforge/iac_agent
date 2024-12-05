import boto3
import json
import time
import uuid
from botocore.exceptions import ClientError
from datetime import datetime, timedelta
import logging

# Constants
CONFIG_RESOURCES_FILE = 'config_resources.json'
TEST_RESOURCE_PREFIX = 'validation-test-bucket'
VALIDATION_TIMEOUT = 300  # seconds to wait for AWS Config to process changes
POLL_INTERVAL = 10  # seconds between SQS polling attempts
LOG_FILE = 'validate_config_pipeline.log'

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)

def load_resource_info(file_path):
    """Load resource information from JSON file."""
    try:
        with open(file_path, 'r') as file:
            resource_info = json.load(file)
        logging.info(f"Loaded resource information from '{file_path}'.")
        return resource_info
    except FileNotFoundError:
        logging.error(f"Configuration file '{file_path}' not found.")
        exit(1)
    except json.JSONDecodeError as e:
        logging.error(f"Error decoding JSON from '{file_path}': {e}")
        exit(1)

def list_all_regions():
    """List all available AWS regions."""
    ec2_client = boto3.client('ec2')
    try:
        regions = [region['RegionName'] for region in ec2_client.describe_regions()['Regions']]
        logging.info(f"Retrieved {len(regions)} regions.")
        return regions
    except ClientError as e:
        logging.error(f"Error retrieving regions: {e}")
        exit(1)

def create_test_s3_bucket(region, bucket_name):
    """Create a test S3 bucket in the specified region."""
    s3_client = boto3.client('s3', region_name=region)
    try:
        if region == 'us-east-1':
            s3_client.create_bucket(Bucket=bucket_name)
        else:
            s3_client.create_bucket(
                Bucket=bucket_name,
                CreateBucketConfiguration={'LocationConstraint': region}
            )
        logging.info(f"Created test S3 bucket '{bucket_name}' in region '{region}'.")
        return True
    except s3_client.exceptions.BucketAlreadyExists:
        logging.warning(f"Bucket '{bucket_name}' already exists globally. Skipping creation.")
        return False
    except s3_client.exceptions.BucketAlreadyOwnedByYou:
        logging.warning(f"Bucket '{bucket_name}' already exists in region '{region}'.")
        return False
    except ClientError as e:
        logging.error(f"Error creating S3 bucket '{bucket_name}' in region '{region}': {e}")
        return False

def delete_test_s3_bucket(region, bucket_name):
    """Delete the test S3 bucket and its contents."""
    s3_client = boto3.client('s3', region_name=region)
    try:
        # Delete all object versions and delete markers
        paginator = s3_client.get_paginator('list_object_versions')
        for page in paginator.paginate(Bucket=bucket_name):
            versions = page.get('Versions', []) + page.get('DeleteMarkers', [])
            for version in versions:
                s3_client.delete_object(Bucket=bucket_name, Key=version['Key'], VersionId=version['VersionId'])
                logging.debug(f"Deleted object '{version['Key']}' (VersionId: {version['VersionId']}) from bucket '{bucket_name}'.")
        
        # Delete the bucket
        s3_client.delete_bucket(Bucket=bucket_name)
        logging.info(f"Deleted test S3 bucket '{bucket_name}' from region '{region}'.")
    except ClientError as e:
        logging.error(f"Error deleting S3 bucket '{bucket_name}' from region '{region}': {e}")

def generate_unique_bucket_name(base_name):
    """Generate a globally unique bucket name."""
    unique_suffix = str(uuid.uuid4())
    return f"{base_name}-{unique_suffix}"

def poll_sqs_for_messages(sqs_client, queue_url, expected_messages, timeout):
    """Poll the SQS queue for expected messages within the timeout period."""
    end_time = datetime.utcnow() + timedelta(seconds=timeout)
    received_messages = []
    while datetime.utcnow() < end_time and len(received_messages) < expected_messages:
        try:
            response = sqs_client.receive_message(
                QueueUrl=queue_url,
                MaxNumberOfMessages=10,
                WaitTimeSeconds=20  # Long polling
            )
            messages = response.get('Messages', [])
            if not messages:
                logging.debug("No messages received in this poll.")
                continue
            for msg in messages:
                try:
                    body = json.loads(msg['Body'])
                    logging.debug(f"Received SQS message body: {json.dumps(body, indent=2)}")
                    
                    message_content = body.get('Message')
                    if not message_content:
                        logging.warning("No 'Message' key found or 'Message' is empty in the SQS message.")
                        # Optionally, handle other parts of the message
                        # For example, check 'Subject' or other keys
                        # Here, we skip processing this message
                        continue
                    
                    try:
                        config_message = json.loads(message_content)
                        logging.debug(f"Parsed Config Message: {json.dumps(config_message, indent=2)}")
                        received_messages.append(config_message)
                    except json.JSONDecodeError:
                        logging.warning("Message content is not valid JSON. Handling as plain text.")
                        config_message = {"plain_text_message": message_content}
                        received_messages.append(config_message)
                    
                    # Delete the message from the queue
                    sqs_client.delete_message(
                        QueueUrl=queue_url,
                        ReceiptHandle=msg['ReceiptHandle']
                    )
                    message_type = config_message.get('messageType', 'Unknown')
                    logging.info(f"Received and deleted message from SQS: {message_type}")
                except json.JSONDecodeError as jde:
                    logging.error(f"JSON decoding failed for message: {msg['Body']}. Error: {jde}")
                except Exception as e:
                    logging.error(f"Unexpected error processing message: {e}")
        except ClientError as e:
            logging.error(f"Error polling SQS: {e}")
            break
    return len(received_messages)

def verify_dynamodb_entries(dynamodb_resource, table_name, expected_entries, timeout):
    """Verify that DynamoDB has the expected number of entries."""
    table = dynamodb_resource.Table(table_name)
    end_time = datetime.utcnow() + timedelta(seconds=timeout)
    while datetime.utcnow() < end_time:
        try:
            response = table.scan(ProjectionExpression="ResourceId")
            items = response.get('Items', [])
            if len(items) >= expected_entries:
                logging.info(f"DynamoDB table '{table_name}' has at least {expected_entries} entries.")
                return True
            else:
                logging.info(f"DynamoDB table '{table_name}' has {len(items)} entries. Waiting for more...")
                time.sleep(POLL_INTERVAL)
        except ClientError as e:
            logging.error(f"Error scanning DynamoDB table '{table_name}': {e}")
            return False
    logging.warning(f"DynamoDB table '{table_name}' did not receive the expected number of entries within the timeout period.")
    return False

def main():
    # Step 1: Load resource information
    resource_info = load_resource_info(CONFIG_RESOURCES_FILE)
    
    # Extract necessary resource ARNs and names
    sqs_queue_url = resource_info.get('SQSQueue', {}).get('QueueUrl')
    sqs_queue_arn = resource_info.get('SQSQueue', {}).get('QueueARN')
    sns_topic_arn = resource_info.get('SNSTopic', {}).get('TopicARN')
    dynamodb_table_name = resource_info.get('DynamoDBTable', {}).get('TableName')
    aggregator_region = resource_info.get('AggregatorRegion', 'us-east-1')
    
    if not all([sqs_queue_url, sqs_queue_arn, sns_topic_arn, dynamodb_table_name]):
        logging.error("Missing necessary resource information in the configuration file.")
        exit(1)
    
    # Initialize clients
    sqs_client = boto3.client('sqs', region_name=aggregator_region)
    dynamodb_resource = boto3.resource('dynamodb', region_name=aggregator_region)
    
    # Step 2: List all available regions
    regions = list_all_regions()
    
    # Step 3: Create test S3 buckets in all regions
    test_buckets = []
    logging.info("Creating test S3 buckets in all regions...")
    for region in regions:
        unique_bucket_name = generate_unique_bucket_name(TEST_RESOURCE_PREFIX)
        created = create_test_s3_bucket(region, unique_bucket_name)
        if created:
            test_buckets.append((region, unique_bucket_name))
        # Sleep briefly to avoid hitting API rate limits
        time.sleep(1)
    
    if not test_buckets:
        logging.error("No test S3 buckets were created. Exiting validation.")
        exit(1)
    
    # Step 4: Wait for AWS Config to process the changes
    logging.info(f"Waiting up to {VALIDATION_TIMEOUT} seconds for AWS Config to process changes...")
    start_time = time.time()
    messages_received = 0
    expected_messages = len(test_buckets)
    
    while time.time() - start_time < VALIDATION_TIMEOUT and messages_received < expected_messages:
        remaining_time = VALIDATION_TIMEOUT - int(time.time() - start_time)
        messages_received = poll_sqs_for_messages(sqs_client, sqs_queue_url, expected_messages, remaining_time)
        if messages_received >= expected_messages:
            break
        time.sleep(POLL_INTERVAL)
    
    if messages_received >= expected_messages:
        logging.info(f"Successfully received {messages_received} messages from SQS.")
    else:
        logging.warning(f"Expected {expected_messages} messages but received {messages_received}.")
    
    # Step 5: (Optional) Verify DynamoDB entries
    logging.info("Verifying DynamoDB entries...")
    entries_verified = verify_dynamodb_entries(dynamodb_resource, dynamodb_table_name, expected_messages, VALIDATION_TIMEOUT)
    if entries_verified:
        logging.info("DynamoDB verification successful.")
    else:
        logging.warning("DynamoDB verification failed.")
    
    # Step 6: Clean up test S3 buckets
    logging.info("Cleaning up test S3 buckets...")
    for region, bucket_name in test_buckets:
        delete_test_s3_bucket(region, bucket_name)
        # Sleep briefly to avoid hitting API rate limits
        time.sleep(1)
    
    # Summary
    logging.info("\nValidation Summary:")
    logging.info(f"Test S3 Buckets Created and Deleted: {len(test_buckets)}")
    logging.info(f"SQS Messages Received: {messages_received} out of {expected_messages}")
    logging.info(f"DynamoDB Entries Verified: {'Yes' if entries_verified else 'No'}")
    
    if messages_received >= expected_messages and entries_verified:
        logging.info("AWS Config pipeline validation successful. All components are functioning correctly.")
    else:
        logging.warning("AWS Config pipeline validation encountered issues. Please review the logs above for details.")

if __name__ == '__main__':
    main()
