import boto3
import json
import time
import os
import re
import requests
import logging
from botocore.exceptions import ClientError
from dotenv import load_dotenv
import psycopg2
from botocore.config import Config as BotoConfig

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.StreamHandler()
    ]
)

# Load environment variables from .env file
load_dotenv()

# Configuration Variables
AWS_ACCESS_KEY_ID = os.getenv('AWS_ACCESS_KEY_ID')
AWS_SECRET_ACCESS_KEY = os.getenv('AWS_SECRET_ACCESS_KEY')
AWS_REGION = os.getenv('AWS_REGION')

S3_BUCKET_NAME = os.getenv('S3_BUCKET_NAME')
CONFIG_ROLE_NAME = os.getenv('CONFIG_ROLE_NAME', 'AWSConfigRole')
LAMBDA_ROLE_NAME = os.getenv('LAMBDA_ROLE_NAME', 'LambdaAWSConfigIngestionRole')
LAMBDA_FUNCTION_NAME = os.getenv('LAMBDA_FUNCTION_NAME', 'AWSConfigIngestionFunction')
LAMBDA_HANDLER = os.getenv('LAMBDA_HANDLER', 'lambda_function.lambda_handler')
LAMBDA_ZIP_PATH = os.getenv('LAMBDA_ZIP_PATH', 'lambda_function.zip')

RDS_DB_IDENTIFIER = os.getenv('RDS_DB_IDENTIFIER', 'forge-aws-config-db')
RDS_DB_NAME = os.getenv('RDS_DB_NAME', 'awsconfigdb')
RDS_DB_USERNAME = os.getenv('RDS_DB_USERNAME', 'admin')
RDS_DB_PASSWORD = os.getenv('RDS_DB_PASSWORD', 'YourSecurePassword123!')
RDS_DB_INSTANCE_CLASS = os.getenv('RDS_DB_INSTANCE_CLASS', 'db.t3.micro')
RDS_DB_ENGINE = os.getenv('RDS_DB_ENGINE', 'postgres')
RDS_DB_ENGINE_VERSION = os.getenv('RDS_DB_ENGINE_VERSION', '16.4')
RDS_DB_ALLOCATED_STORAGE = int(os.getenv('RDS_DB_ALLOCATED_STORAGE', '20'))

SECRET_NAME = os.getenv('SECRET_NAME', 'AWSConfigDBCredentials')

# Optional VPC Configuration
VPC_SUBNET_IDS = os.getenv('VPC_SUBNET_IDS', '')  # Comma-separated subnet IDs
VPC_SECURITY_GROUP_IDS = os.getenv('VPC_SECURITY_GROUP_IDS', '')  # Comma-separated security group IDs

# AWS Account ID (Required for custom policy creation)
ACCOUNT_ID = os.getenv('ACCOUNT_ID')

if not ACCOUNT_ID:
    logging.error("ACCOUNT_ID is not set in the .env file.")
    exit(1)

# Initialize boto3 clients with retry configuration
boto_config = BotoConfig(
    retries={
        'max_attempts': 10,
        'mode': 'standard'
    }
)

session = boto3.Session(
    aws_access_key_id=AWS_ACCESS_KEY_ID,
    aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
    region_name=AWS_REGION
)

s3_client = session.client('s3', config=boto_config)
iam_client = session.client('iam', config=boto_config)
config_client = session.client('config', config=boto_config)
rds_client = session.client('rds', config=boto_config)
secrets_client = session.client('secretsmanager', config=boto_config)
lambda_client = session.client('lambda', config=boto_config)
events_client = session.client('events', config=boto_config)
ec2_client = session.client('ec2', config=boto_config)  # Added EC2 client here

def get_public_ip():
    try:
        response = requests.get('https://api.ipify.org?format=json', timeout=10)
        response.raise_for_status()
        ip = response.json()['ip']
        logging.info(f"Detected public IP address: {ip}")
        return ip
    except requests.RequestException as e:
        logging.error(f"Error fetching public IP address: {e}")
        raise

def create_security_group(security_group_name, description, ingress_rules):
    try:
        # Describe VPCs to get the VPC ID
        vpc_response = ec2_client.describe_vpcs()
        if not vpc_response['Vpcs']:
            logging.error("No VPCs found in your account.")
            raise Exception("No VPCs available.")
        vpc_id = vpc_response['Vpcs'][0]['VpcId']
        logging.info(f"Using VPC ID: {vpc_id}")
    except ClientError as e:
        logging.error(f"Error retrieving VPC ID: {e}")
        raise

    try:
        # First try to find the security group by filters
        response = ec2_client.describe_security_groups(
            Filters=[
                {'Name': 'group-name', 'Values': [security_group_name]},
                {'Name': 'vpc-id', 'Values': [vpc_id]}
            ]
        )
        
        if response['SecurityGroups']:
            # Security group exists
            security_group_id = response['SecurityGroups'][0]['GroupId']
            logging.info(f"Found existing security group '{security_group_name}' with ID '{security_group_id}'.")
            
            # Get current security group rules
            sg_info = ec2_client.describe_security_groups(GroupIds=[security_group_id])
            existing_rules = sg_info['SecurityGroups'][0]['IpPermissions']
            
            # First, revoke all existing ingress rules
            if existing_rules:
                logging.info("Revoking existing ingress rules to ensure clean state...")
                ec2_client.revoke_security_group_ingress(
                    GroupId=security_group_id,
                    IpPermissions=existing_rules
                )
                
            # Add new ingress rule
            try:
                ec2_client.authorize_security_group_ingress(
                    GroupId=security_group_id,
                    IpPermissions=ingress_rules
                )
                logging.info(f"Added fresh ingress rules to security group '{security_group_name}'")
            except ClientError as e:
                if e.response['Error']['Code'] != 'InvalidPermission.Duplicate':
                    raise
                
        else:
            # Create new security group
            response = ec2_client.create_security_group(
                GroupName=security_group_name,
                Description=description,
                VpcId=vpc_id
            )
            security_group_id = response['GroupId']
            logging.info(f"Created new security group '{security_group_name}' with ID '{security_group_id}'.")
            
            # Add ingress rules
            ec2_client.authorize_security_group_ingress(
                GroupId=security_group_id,
                IpPermissions=ingress_rules
            )
            logging.info(f"Added ingress rules to new security group '{security_group_name}'")

        # Verify the rules are correctly applied
        verify_response = ec2_client.describe_security_groups(GroupIds=[security_group_id])
        current_rules = verify_response['SecurityGroups'][0]['IpPermissions']
        logging.info(f"Current security group rules: {json.dumps(current_rules, indent=2)}")

        return security_group_id

    except ClientError as e:
        logging.error(f"Error managing security group '{security_group_name}': {e}")
        raise

def create_s3_bucket(bucket_name, region):
    # Validate bucket name format
    VALID_BUCKET = re.compile(r'^[a-z0-9.-]{3,63}$')
    VALID_S3_ARN = re.compile(r'^arn:aws:s3:::[a-z0-9.-]{3,63}$')

    if not bucket_name or not isinstance(bucket_name, str):
        raise ValueError("S3_BUCKET_NAME must be a non-empty string.")

    if not (VALID_BUCKET.match(bucket_name) or VALID_S3_ARN.match(bucket_name)):
        raise ValueError(f"Invalid S3 bucket name: {bucket_name}")

    try:
        if region == 'us-east-1':
            # Create bucket without LocationConstraint
            response = s3_client.create_bucket(
                Bucket=bucket_name
            )
        else:
            # Create bucket with LocationConstraint for other regions
            response = s3_client.create_bucket(
                Bucket=bucket_name,
                CreateBucketConfiguration={'LocationConstraint': region}
            )
        logging.info(f"S3 bucket '{bucket_name}' created successfully.")
    except ClientError as e:
        error_code = e.response['Error']['Code']
        if error_code == 'BucketAlreadyOwnedByYou':
            logging.info(f"S3 bucket '{bucket_name}' already exists and is owned by you.")
        elif error_code == 'BucketAlreadyExists':
            logging.error(f"S3 bucket '{bucket_name}' already exists and is owned by another account.")
            raise
        else:
            logging.error(f"Error creating S3 bucket: {e}")
            raise

def create_iam_role(role_name, assume_role_policy_document, description=''):
    try:
        response = iam_client.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(assume_role_policy_document),
            Description=description
        )
        logging.info(f"IAM role '{role_name}' created successfully.")
        return response['Role']['Arn']
    except ClientError as e:
        error_code = e.response['Error']['Code']
        if error_code == 'EntityAlreadyExists':
            logging.info(f"IAM role '{role_name}' already exists.")
            role = iam_client.get_role(RoleName=role_name)
            return role['Role']['Arn']
        else:
            logging.error(f"Error creating IAM role '{role_name}': {e}")
            raise

def attach_policy_to_role(role_name, policy_arn):
    try:
        iam_client.attach_role_policy(
            RoleName=role_name,
            PolicyArn=policy_arn
        )
        logging.info(f"Policy '{policy_arn}' attached to role '{role_name}'.")
    except ClientError as e:
        logging.error(f"Error attaching policy '{policy_arn}' to role '{role_name}': {e}")
        raise

def check_policy_exists(policy_arn):
    try:
        iam_client.get_policy(PolicyArn=policy_arn)
        return True
    except ClientError as e:
        if e.response['Error']['Code'] == 'NoSuchEntity':
            return False
        else:
            logging.error(f"Error checking policy existence: {e}")
            raise

def create_custom_policy(role_name, bucket_name, account_id):
    policy_document = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": [
                    "s3:GetBucketAcl",
                    "s3:PutObject",
                    "s3:GetObject",
                    "s3:ListBucket"
                ],
                "Resource": [
                    f"arn:aws:s3:::{bucket_name}",
                    f"arn:aws:s3:::{bucket_name}/*"
                ]
            },
            {
                "Effect": "Allow",
                "Action": [
                    "config:Put*",
                    "config:Get*",
                    "config:Describe*"
                ],
                "Resource": "*"
            },
            {
                "Effect": "Allow",
                "Action": [
                    "iam:PassRole"
                ],
                "Resource": f"arn:aws:iam::{account_id}:role/{CONFIG_ROLE_NAME}"
            }
        ]
    }

    policy_name = f"{CONFIG_ROLE_NAME}Policy-{account_id}"
    policy_arn = f"arn:aws:iam::{account_id}:policy/{policy_name}"

    try:
        response = iam_client.create_policy(
            PolicyName=policy_name,
            PolicyDocument=json.dumps(policy_document),
            Description='Custom policy for AWS Config role.'
        )
        policy_arn = response['Policy']['Arn']
        logging.info(f"Custom policy '{policy_name}' created successfully.")
    except ClientError as e:
        if e.response['Error']['Code'] == 'EntityAlreadyExists':
            logging.info(f"Custom policy '{policy_name}' already exists.")
            policy_arn = f"arn:aws:iam::{account_id}:policy/{policy_name}"
        else:
            logging.error(f"Error creating custom policy: {e}")
            raise

    attach_policy_to_role(role_name, policy_arn)

def put_inline_policy(role_name, policy_name, policy_document):
    try:
        iam_client.put_role_policy(
            RoleName=role_name,
            PolicyName=policy_name,
            PolicyDocument=json.dumps(policy_document)
        )
        logging.info(f"Inline policy '{policy_name}' added to role '{role_name}'.")
    except ClientError as e:
        logging.error(f"Error adding inline policy '{policy_name}' to role '{role_name}': {e}")
        raise

def setup_aws_config(bucket_name, role_arn):
    try:
        # Create Configuration Recorder
        config_client.put_configuration_recorder(
            ConfigurationRecorder={
                'name': 'default',
                'roleARN': role_arn,
                'recordingGroup': {
                    'allSupported': True,
                    'includeGlobalResourceTypes': True
                }
            }
        )
        logging.info("AWS Config Configuration Recorder created.")
    except ClientError as e:
        logging.error(f"Error creating Configuration Recorder: {e}")
        raise

    try:
        # Create Delivery Channel
        delivery_channel = {
            'name': 'default',
            's3BucketName': bucket_name,
            'configSnapshotDeliveryProperties': {
                'deliveryFrequency': 'TwentyFour_Hours'
            }
        }
        config_client.put_delivery_channel(
            DeliveryChannel=delivery_channel
        )
        logging.info("AWS Config Delivery Channel created.")
    except ClientError as e:
        logging.error(f"Error creating Delivery Channel: {e}")
        raise

    try:
        # Start Configuration Recorder
        config_client.start_configuration_recorder(ConfigurationRecorderName='default')
        logging.info("AWS Config Configuration Recorder started.")
    except ClientError as e:
        logging.error(f"Error starting Configuration Recorder: {e}")
        raise

def get_or_create_db_subnet_group(vpc_id):
    try:
        # Describe subnets in the specified VPC
        subnets_response = ec2_client.describe_subnets(
            Filters=[{'Name': 'vpc-id', 'Values': [vpc_id]}]
        )
        subnet_ids = [subnet['SubnetId'] for subnet in subnets_response['Subnets']]
        
        # Check if a DB subnet group already exists
        db_subnet_groups = rds_client.describe_db_subnet_groups()
        for db_subnet_group in db_subnet_groups['DBSubnetGroups']:
            # Extract SubnetIdentifier from each Subnet in the group
            group_subnet_ids = [subnet['SubnetIdentifier'] for subnet in db_subnet_group['Subnets']]
            if set(subnet_ids).issubset(set(group_subnet_ids)):
                logging.info(f"Using existing DB subnet group: {db_subnet_group['DBSubnetGroupName']}")
                return db_subnet_group['DBSubnetGroupName']
        
        # Create a new DB subnet group if none exists
        db_subnet_group_name = f"db-subnet-group-{vpc_id}"
        rds_client.create_db_subnet_group(
            DBSubnetGroupName=db_subnet_group_name,
            DBSubnetGroupDescription='DB subnet group for RDS instance',
            SubnetIds=subnet_ids
        )
        logging.info(f"Created new DB subnet group: {db_subnet_group_name}")
        return db_subnet_group_name
    except ClientError as e:
        logging.error(f"Error handling DB subnet group: {e}")
        raise

def create_rds_instance(security_group_id, vpc_id):
    try:
        db_subnet_group_name = get_or_create_db_subnet_group(vpc_id)
        response = rds_client.create_db_instance(
            DBInstanceIdentifier=RDS_DB_IDENTIFIER,
            DBName=RDS_DB_NAME,
            AllocatedStorage=RDS_DB_ALLOCATED_STORAGE,
            DBInstanceClass=RDS_DB_INSTANCE_CLASS,
            Engine=RDS_DB_ENGINE,
            EngineVersion=RDS_DB_ENGINE_VERSION,
            MasterUsername=RDS_DB_USERNAME,
            MasterUserPassword=RDS_DB_PASSWORD,
            BackupRetentionPeriod=7,
            MultiAZ=False,
            PubliclyAccessible=True,
            StorageType='gp2',
            AutoMinorVersionUpgrade=True,
            Tags=[
                {
                    'Key': 'Name',
                    'Value': 'AWSConfigDB'
                },
            ],
            VpcSecurityGroupIds=[security_group_id],
            DBSubnetGroupName=db_subnet_group_name
        )
        logging.info(f"RDS instance '{RDS_DB_IDENTIFIER}' creation initiated.")
    except ClientError as e:
        if e.response['Error']['Code'] == 'DBInstanceAlreadyExists':
            logging.info(f"RDS instance '{RDS_DB_IDENTIFIER}' already exists.")
        else:
            logging.error(f"Error creating RDS instance: {e}")
            raise

def create_secret():
    try:
        secrets_client.create_secret(
            Name=SECRET_NAME,
            SecretString=json.dumps({
                'username': RDS_DB_USERNAME,
                'password': RDS_DB_PASSWORD
            })
        )
        logging.info(f"Secret '{SECRET_NAME}' created successfully.")
    except ClientError as e:
        if e.response['Error']['Code'] == 'ResourceExistsException':
            logging.info(f"Secret '{SECRET_NAME}' already exists.")
        else:
            logging.error(f"Error creating secret '{SECRET_NAME}': {e}")
            raise

def create_lambda_function(role_arn, db_host):
    try:
        with open(LAMBDA_ZIP_PATH, 'rb') as f:
            zipped_code = f.read()

        env_vars = {
            'SECRET_NAME': SECRET_NAME,
            'DB_NAME': RDS_DB_NAME,
            'DB_HOST': db_host
        }

        # Include VPC configuration if provided
        vpc_config = {}
        if VPC_SUBNET_IDS and VPC_SECURITY_GROUP_IDS:
            subnet_ids = [subnet.strip() for subnet in VPC_SUBNET_IDS.split(',')]
            security_group_ids = [sg.strip() for sg in VPC_SECURITY_GROUP_IDS.split(',')]
            vpc_config = {
                'VpcConfig': {
                    'SubnetIds': subnet_ids,
                    'SecurityGroupIds': security_group_ids
                }
            }

        response = lambda_client.create_function(
            FunctionName=LAMBDA_FUNCTION_NAME,
            Runtime='python3.9',
            Role=role_arn,
            Handler=LAMBDA_HANDLER,
            Code={'ZipFile': zipped_code},
            Description='Lambda function to ingest AWS Config data into RDS',
            Timeout=300,
            MemorySize=256,
            Publish=True,
            Environment={
                'Variables': env_vars
            },
            **vpc_config  # Unpack VPC configuration if present
        )
        logging.info(f"Lambda function '{LAMBDA_FUNCTION_NAME}' created successfully.")
    except ClientError as e:
        if e.response['Error']['Code'] == 'ResourceConflictException':
            logging.info(f"Lambda function '{LAMBDA_FUNCTION_NAME}' already exists. Updating code...")
            try:
                with open(LAMBDA_ZIP_PATH, 'rb') as f:
                    zipped_code = f.read()
                lambda_client.update_function_code(
                    FunctionName=LAMBDA_FUNCTION_NAME,
                    ZipFile=zipped_code,
                    Publish=True
                )
                logging.info(f"Lambda function '{LAMBDA_FUNCTION_NAME}' code updated successfully.")
            except ClientError as update_e:
                logging.error(f"Error updating Lambda function: {update_e}")
                raise
        else:
            logging.error(f"Error creating/updating Lambda function: {e}")
            raise

def get_rds_endpoint():
    """Get the RDS endpoint, waiting for the instance to become available if necessary."""
    try:
        max_attempts = 60  # Maximum number of attempts (10 minutes with 10-second intervals)
        attempt = 0
        
        while attempt < max_attempts:
            response = rds_client.describe_db_instances(DBInstanceIdentifier=RDS_DB_IDENTIFIER)
            instance = response['DBInstances'][0]
            status = instance.get('DBInstanceStatus', '')
            
            if status == 'available' and 'Endpoint' in instance:
                endpoint = instance['Endpoint']['Address']
                logging.info(f"RDS endpoint found: {endpoint}")
                return endpoint
            
            logging.info(f"Waiting for RDS instance to become available... Current status: {status}")
            time.sleep(10)  # Wait 10 seconds between checks
            attempt += 1
        
        raise TimeoutError(f"RDS instance did not become available after {max_attempts * 10} seconds")
    
    except ClientError as e:
        logging.error(f"Error retrieving RDS endpoint: {e}")
        raise
    except TimeoutError as e:
        logging.error(str(e))
        raise

def apply_database_schema():
    # Load environment variables
    db_host = get_rds_endpoint()
    db_name = RDS_DB_NAME
    db_user = RDS_DB_USERNAME
    db_password = RDS_DB_PASSWORD

    # Path to the database_schema.sql
    schema_path = os.path.join(os.getcwd(), 'database_schema.sql')

    # Wait briefly to ensure RDS is ready to accept connections
    logging.info("Waiting 60 seconds for RDS instance to accept connections...")
    time.sleep(60)

    # Implement connection retries with exponential backoff
    max_retries = 10
    wait_time = 10  # Initial wait time in seconds

    for attempt in range(1, max_retries + 1):
        
        if True:
            logging.info(f"Attempt {attempt}: Connecting to the database at {db_host}...")
            conn = psycopg2.connect(
                host=db_host,
                database=db_name,
                user=db_user,
                password=db_password,
                connect_timeout=10  # seconds
            )
            cursor = conn.cursor()
            with open(schema_path, 'r') as f:
                schema_sql = f.read()
            cursor.execute(schema_sql)
            conn.commit()
            cursor.close()
            conn.close()
            logging.info("Database schema applied successfully.")
            break
        #     break  # Exit loop on success
        # except Exception as e:
        #     print(e)

def create_eventbridge_rule():
    event_pattern = {
        "source": ["aws.config"],
        "detail-type": ["Config Rules Compliance Change", "Config Configuration Item Change"]
    }

    try:
        response = events_client.put_rule(
            Name='AWSConfigChangeRule',
            EventPattern=json.dumps(event_pattern),
            State='ENABLED',
            Description='Rule to trigger Lambda on AWS Config changes',
            EventBusName='default'
        )
        rule_arn = response['RuleArn']
        logging.info("EventBridge rule 'AWSConfigChangeRule' created successfully.")
        return rule_arn
    except ClientError as e:
        logging.error(f"Error creating EventBridge rule: {e}")
        raise

def add_lambda_permission(rule_arn):
    try:
        lambda_client.add_permission(
            FunctionName=LAMBDA_FUNCTION_NAME,
            StatementId='EventBridgeInvoke',
            Action='lambda:InvokeFunction',
            Principal='events.amazonaws.com',
            SourceArn=rule_arn
        )
        logging.info("Permission added to Lambda function for EventBridge to invoke it.")
    except ClientError as e:
        if e.response['Error']['Code'] == 'ResourceConflictException':
            logging.info("Permission already exists for Lambda function.")
        else:
            logging.error(f"Error adding permission to Lambda function: {e}")
            raise

def add_lambda_to_eventbridge(rule_arn):
    try:
        lambda_info = lambda_client.get_function(FunctionName=LAMBDA_FUNCTION_NAME)
        lambda_arn = lambda_info['Configuration']['FunctionArn']

        response = events_client.put_targets(
            Rule='AWSConfigChangeRule',
            Targets=[
                {
                    'Id': '1',
                    'Arn': lambda_arn
                }
            ]
        )
        logging.info("Lambda function added as target to EventBridge rule.")
    except ClientError as e:
        logging.error(f"Error adding Lambda function to EventBridge rule: {e}")
        raise

def attach_policies_to_config_role(role_name):
    # Correct AWS managed policy for AWS Config with underscore
    policy_arn = 'arn:aws:iam::aws:policy/service-role/AWS_ConfigRole'
    if check_policy_exists(policy_arn):
        attach_policy_to_role(role_name, policy_arn)
    else:
        # If managed policy does not exist, create and attach a custom policy
        logging.warning(f"Managed policy '{policy_arn}' does not exist. Creating a custom policy.")
        create_custom_policy(role_name, S3_BUCKET_NAME, ACCOUNT_ID)

def attach_policies_to_lambda_role(role_name):
    # AWS managed policies
    managed_policies = [
        'arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole',
        'arn:aws:iam::aws:policy/AmazonS3ReadOnlyAccess',
        'arn:aws:iam::aws:policy/SecretsManagerReadWrite'
    ]
    
    for policy in managed_policies:
        attach_policy_to_role(role_name, policy)

    # Custom inline policy for RDS access
    rds_access_policy = {
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Action": [
                "rds:DescribeDBInstances"
            ],
            "Resource": "*"
        }]
    }
    put_inline_policy(role_name, 'LambdaRDSAccessPolicy', rds_access_policy)

def main():
    try:
        # 1. Create S3 Bucket
        create_s3_bucket(S3_BUCKET_NAME, AWS_REGION)

        # 2. Create IAM Role for AWS Config
        config_assume_role_policy = {
            "Version": "2012-10-17",
            "Statement": [{
                "Effect": "Allow",
                "Principal": {"Service": "config.amazonaws.com"},
                "Action": "sts:AssumeRole"
            }]
        }
        config_role_arn = create_iam_role(
            CONFIG_ROLE_NAME,
            config_assume_role_policy,
            description='Role for AWS Config to access S3'
        )

        # 3. Attach AWS managed policy to Config role
        attach_policies_to_config_role(CONFIG_ROLE_NAME)

        # 4. Setup AWS Config
        setup_aws_config(S3_BUCKET_NAME, config_role_arn)

        # 5. Fetch Public IP Address
        public_ip = get_public_ip()
        logging.info(f"Current public IP: {public_ip}")


        # 6. Get VPC ID first
        vpc_response = ec2_client.describe_vpcs()
        if not vpc_response['Vpcs']:
            raise Exception("No VPCs available.")
        vpc_id = vpc_response['Vpcs'][0]['VpcId']
        logging.info(f"Using VPC ID: {vpc_id}")

        # 7. Create Security Group for RDS
        security_group_name = f"RDS-SG-{RDS_DB_IDENTIFIER}"
        description = "Security group for RDS instance allowing PostgreSQL access"
        ingress_rules = [
            {
                'IpProtocol': 'tcp',
                'FromPort': 5432,
                'ToPort': 5432,
                'IpRanges': [
                    {
                        'CidrIp': f"{public_ip}/32",
                        'Description': 'PostgreSQL access from current IP'
                    }
                ]
            }
        ]
        security_group_id = create_security_group(security_group_name, description, ingress_rules)

        try:
            rds_client.modify_db_instance(
                DBInstanceIdentifier=RDS_DB_IDENTIFIER,
                VpcSecurityGroupIds=[security_group_id],
                ApplyImmediately=True
            )
            logging.info(f"Updated RDS instance security group to: {security_group_id}")
        except ClientError as e:
            if 'DBInstanceNotFound' not in str(e):
                raise

        # 8. Create RDS PostgreSQL Instance - now passing both required parameters
        create_rds_instance(security_group_id, vpc_id)

        # Rest of the function remains the same...
        # 9. Retrieve RDS Endpoint
        rds_endpoint = get_rds_endpoint()
        logging.info(f"RDS Endpoint: {rds_endpoint}")

        # 10. Create Secret in Secrets Manager
        create_secret()

        # 11. Apply Database Schema
        apply_database_schema()

        # 12. Create IAM Role for Lambda
        lambda_assume_role_policy = {
            "Version": "2012-10-17",
            "Statement": [{
                "Effect": "Allow",
                "Principal": {"Service": "lambda.amazonaws.com"},
                "Action": "sts:AssumeRole"
            }]
        }
        lambda_role_arn = create_iam_role(
            LAMBDA_ROLE_NAME,
            lambda_assume_role_policy,
            description='Role for Lambda to access S3, Secrets Manager, and RDS'
        )

        # 13. Attach AWS managed and custom policies to Lambda role
        attach_policies_to_lambda_role(LAMBDA_ROLE_NAME)

        # 14. Deploy Lambda Function
        create_lambda_function(lambda_role_arn, rds_endpoint)

        # 15. Create EventBridge Rule
        rule_arn = create_eventbridge_rule()

        # 16. Add Lambda Permission for EventBridge
        add_lambda_permission(rule_arn)

        # 17. Add Lambda Function as Target to EventBridge Rule
        add_lambda_to_eventbridge(rule_arn)

        logging.info("Setup completed successfully.")
    except Exception as e:
        logging.error(f"Setup failed: {e}")
        raise

if __name__ == "__main__":
    main()
