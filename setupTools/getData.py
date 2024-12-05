import boto3
import json
import time
import zlib
import hashlib
import logging
from botocore.exceptions import ClientError
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler("data_collector.log"),
        logging.StreamHandler()
    ]
)

# Load configuration from config_resources.json
with open('config_resources.json', 'r') as config_file:
    resource_info = json.load(config_file)

AWS_ACCOUNT_ID = resource_info['AWSAccountID']
AGGREGATOR_REGION = resource_info['AggregatorRegion']
DYNAMODB_TABLE_NAME = resource_info['DynamoDBTable']['TableName']
DYNAMODB_REGION = resource_info['DynamoDBTable']['Region']
S3_BUCKET_NAME = resource_info['S3Bucket']['BucketName']
CONFIG_ROLE_ARN = resource_info['IAMRole']['RoleARN']
AGGREGATOR_NAME = resource_info['ConfigAggregator']['AggregatorName']

# SQS queue URL and region from the config file
SQS_QUEUE_URL = resource_info['SQSQueue']['QueueUrl']
SQS_QUEUE_REGION = resource_info['SQSQueue']['Region']

# Initialize AWS clients
dynamodb_resource = boto3.resource('dynamodb', region_name=DYNAMODB_REGION)
s3_client = boto3.client('s3', region_name=AGGREGATOR_REGION)
sqs_client = boto3.client('sqs', region_name=SQS_QUEUE_REGION)
sts_client = boto3.client('sts')
eks_client = boto3.client('eks', region_name=AGGREGATOR_REGION)
cloudwatch_client = boto3.client('cloudwatch', region_name=AGGREGATOR_REGION)
logs_client = boto3.client('logs', region_name=AGGREGATOR_REGION)
eventbridge_client = boto3.client('events', region_name=AGGREGATOR_REGION)

def get_account_id():
    """Retrieve AWS Account ID."""
    try:
        response = sts_client.get_caller_identity()
        return response['Account']
    except ClientError as e:
        logging.error(f"Error retrieving AWS Account ID: {e}")
        raise

# Verify AWS Account ID matches
current_account_id = get_account_id()
if current_account_id != AWS_ACCOUNT_ID:
    logging.error("AWS Account ID does not match the one in the configuration file.")
    exit(1)

def get_all_regions():
    """Retrieve all AWS regions."""
    try:
        ec2 = boto3.client('ec2', region_name=AGGREGATOR_REGION)
        regions_response = ec2.describe_regions(AllRegions=False)
        regions = [region['RegionName'] for region in regions_response['Regions']]
        logging.info(f"Retrieved regions: {regions}")
        return regions
    except ClientError as e:
        logging.error(f"Error retrieving AWS regions: {e}")
        raise

def store_data_in_dynamodb(resource_id, resource_type, configuration, region, configuration_capture_time, tags=None):
    """Store resource data in DynamoDB, handling size limits."""
    table = dynamodb_resource.Table(DYNAMODB_TABLE_NAME)
    item = {
        'ResourceId': resource_id,
        'ConfigurationItemCaptureTime': configuration_capture_time,  # Correct Sort Key
        'ResourceType': resource_type,
        'AWSRegion': region,
    }
    if tags:
        item['Tags'] = tags

    # Compress and encode the configuration if it exceeds DynamoDB item size limit
    config_str = json.dumps(configuration, default=str)
    config_size = len(config_str.encode('utf-8'))
    if config_size > 350000:  # DynamoDB item size limit is 400KB
        logging.info(f"Configuration for resource {resource_id} is large ({config_size} bytes). Storing in S3.")
        s3_key = f"{resource_type}/{resource_id}_{configuration_capture_time}.json.gz"
        try:
            compressed_data = zlib.compress(config_str.encode('utf-8'))
            s3_client.put_object(Bucket=S3_BUCKET_NAME, Key=s3_key, Body=compressed_data)
            item['ConfigurationS3Key'] = s3_key
        except ClientError as e:
            logging.error(f"Error storing configuration in S3 for resource {resource_id}: {e}")
            return
    else:
        item['Configuration'] = config_str

    try:
        table.put_item(Item=item)
        logging.info(f"Stored data for resource '{resource_id}' of type '{resource_type}'.")
    except ClientError as e:
        logging.error(f"Error storing data in DynamoDB for resource {resource_id}: {e}")

# Data collection functions for various AWS services

def collect_ec2_data(region):
    """Collect EC2 instances, security groups, key pairs, and volumes."""
    logging.info(f"Collecting EC2 data in region: {region}")
    try:
        ec2 = boto3.client('ec2', region_name=region)
        
        # Collect Instances
        paginator = ec2.get_paginator('describe_instances')
        for page in paginator.paginate():
            for reservation in page.get('Reservations', []):
                for instance in reservation.get('Instances', []):
                    tags = {tag['Key']: tag['Value'] for tag in instance.get('Tags', [])} if 'Tags' in instance else None
                    launch_time = instance.get('LaunchTime', datetime.utcnow()).isoformat()
                    store_data_in_dynamodb(
                        resource_id=instance['InstanceId'],
                        resource_type='AWS::EC2::Instance',
                        configuration=instance,
                        region=region,
                        configuration_capture_time=launch_time,
                        tags=tags
                    )
        
        # Collect Security Groups
        paginator = ec2.get_paginator('describe_security_groups')
        for page in paginator.paginate():
            for sg in page.get('SecurityGroups', []):
                tags = {tag['Key']: tag['Value'] for tag in sg.get('Tags', [])} if 'Tags' in sg else None
                description = sg.get('Description', datetime.utcnow().isoformat())
                store_data_in_dynamodb(
                    resource_id=sg['GroupId'],
                    resource_type='AWS::EC2::SecurityGroup',
                    configuration=sg,
                    region=region,
                    configuration_capture_time=description,
                    tags=tags
                )
        
        # Collect Volumes
        paginator = ec2.get_paginator('describe_volumes')
        for page in paginator.paginate():
            for volume in page.get('Volumes', []):
                tags = {tag['Key']: tag['Value'] for tag in volume.get('Tags', [])} if 'Tags' in volume else None
                create_time = volume.get('CreateTime', datetime.utcnow()).isoformat()
                store_data_in_dynamodb(
                    resource_id=volume['VolumeId'],
                    resource_type='AWS::EC2::Volume',
                    configuration=volume,
                    region=region,
                    configuration_capture_time=create_time,
                    tags=tags
                )
        
        # Collect Key Pairs without paginator
        try:
            key_pairs = ec2.describe_key_pairs()['KeyPairs']
            for kp in key_pairs:
                # Note: Key Pairs don't have a capture time, use a placeholder or current time
                capture_time = datetime.utcnow().isoformat()
                store_data_in_dynamodb(
                    resource_id=kp['KeyPairId'],
                    resource_type='AWS::EC2::KeyPair',
                    configuration=kp,
                    region=region,
                    configuration_capture_time=capture_time
                )
        except ClientError as e:
            logging.error(f"Error describing key pairs in region {region}: {e}")
        except Exception as e:
            logging.error(f"Unexpected error describing key pairs in region {region}: {e}")
        
    except Exception as e:
        logging.error(f"Error collecting EC2 data in region {region}: {e}")

def collect_s3_data():
    """Collect S3 buckets and bucket policies."""
    logging.info("Collecting S3 data")
    try:
        s3 = boto3.client('s3', region_name=AGGREGATOR_REGION)
        response = s3.list_buckets()
        buckets = response.get('Buckets', [])
        for bucket in buckets:
            bucket_name = bucket['Name']
            try:
                bucket_location = s3.get_bucket_location(Bucket=bucket_name)['LocationConstraint'] or 'us-east-1'
            except ClientError as e:
                logging.error(f"Error getting location for bucket {bucket_name}: {e}")
                bucket_location = 'us-east-1'
            bucket_info = {
                'Name': bucket_name,
                'CreationDate': bucket['CreationDate'].isoformat(),
                'Region': bucket_location
            }
            # Get bucket policy if exists
            try:
                policy = s3.get_bucket_policy(Bucket=bucket_name)
                bucket_info['Policy'] = policy['Policy']
            except s3.exceptions.NoSuchBucketPolicy:
                bucket_info['Policy'] = None
            except ClientError as e:
                logging.error(f"Error getting bucket policy for {bucket_name}: {e}")
                bucket_info['Policy'] = None
            # Get bucket encryption configuration
            try:
                encryption = s3.get_bucket_encryption(Bucket=bucket_name)
                bucket_info['Encryption'] = encryption['ServerSideEncryptionConfiguration']
            except s3.exceptions.ServerSideEncryptionConfigurationNotFoundError:
                bucket_info['Encryption'] = None
            except ClientError as e:
                logging.error(f"Error getting bucket encryption for {bucket_name}: {e}")
                bucket_info['Encryption'] = None
            # Use bucket creation time as capture time
            capture_time = bucket['CreationDate'].isoformat()
            store_data_in_dynamodb(
                resource_id=bucket_name,
                resource_type='AWS::S3::Bucket',
                configuration=bucket_info,
                region=bucket_location,
                configuration_capture_time=capture_time
            )
    except Exception as e:
        logging.error(f"Error collecting S3 data: {e}")

def collect_iam_data():
    """Collect IAM users, roles, policies, and groups."""
    logging.info("Collecting IAM data")
    try:
        iam = boto3.client('iam')
        # Users
        paginator = iam.get_paginator('list_users')
        for page in paginator.paginate():
            for user in page.get('Users', []):
                user_name = user['UserName']
                try:
                    user_detail = iam.get_user(UserName=user_name)['User']
                    tags = {tag['Key']: tag['Value'] for tag in user_detail.get('Tags', [])} if 'Tags' in user_detail else None
                except ClientError as e:
                    logging.error(f"Error getting details for IAM user {user_name}: {e}")
                    user_detail = user
                    tags = None
                capture_time = user_detail.get('CreateDate', datetime.utcnow()).isoformat()
                store_data_in_dynamodb(
                    resource_id=user_detail['UserId'],
                    resource_type='AWS::IAM::User',
                    configuration=user_detail,
                    region='global',
                    configuration_capture_time=capture_time,
                    tags=tags
                )
        # Roles
        paginator = iam.get_paginator('list_roles')
        for page in paginator.paginate():
            for role in page.get('Roles', []):
                role_name = role['RoleName']
                try:
                    role_detail = iam.get_role(RoleName=role_name)['Role']
                    tags = {tag['Key']: tag['Value'] for tag in role_detail.get('Tags', [])} if 'Tags' in role_detail else None
                except ClientError as e:
                    logging.error(f"Error getting details for IAM role {role_name}: {e}")
                    role_detail = role
                    tags = None
                capture_time = role_detail.get('CreateDate', datetime.utcnow()).isoformat()
                store_data_in_dynamodb(
                    resource_id=role_detail['RoleId'],
                    resource_type='AWS::IAM::Role',
                    configuration=role_detail,
                    region='global',
                    configuration_capture_time=capture_time,
                    tags=tags
                )
        # Policies
        paginator = iam.get_paginator('list_policies')
        for page in paginator.paginate(Scope='Local'):
            for policy in page.get('Policies', []):
                policy_name = policy['PolicyName']
                try:
                    policy_detail = iam.get_policy(PolicyArn=policy['Arn'])['Policy']
                    capture_time = policy_detail.get('CreateDate', datetime.utcnow()).isoformat()
                    store_data_in_dynamodb(
                        resource_id=policy_detail['PolicyId'],
                        resource_type='AWS::IAM::Policy',
                        configuration=policy_detail,
                        region='global',
                        configuration_capture_time=capture_time
                    )
                except ClientError as e:
                    logging.error(f"Error getting details for IAM policy {policy_name}: {e}")
        # Groups
        paginator = iam.get_paginator('list_groups')
        for page in paginator.paginate():
            for group in page.get('Groups', []):
                group_name = group['GroupName']
                try:
                    group_detail = iam.get_group(GroupName=group_name)['Group']
                    tags = {tag['Key']: tag['Value'] for tag in group_detail.get('Tags', [])} if 'Tags' in group_detail else None
                except ClientError as e:
                    logging.error(f"Error getting details for IAM group {group_name}: {e}")
                    group_detail = group
                    tags = None
                capture_time = group_detail.get('CreateDate', datetime.utcnow()).isoformat()
                store_data_in_dynamodb(
                    resource_id=group_detail['GroupId'],
                    resource_type='AWS::IAM::Group',
                    configuration=group_detail,
                    region='global',
                    configuration_capture_time=capture_time,
                    tags=tags
                )
    except Exception as e:
        logging.error(f"Error collecting IAM data: {e}")

def collect_rds_data(region):
    """Collect RDS instances."""
    logging.info(f"Collecting RDS data in region: {region}")
    try:
        rds = boto3.client('rds', region_name=region)
        paginator = rds.get_paginator('describe_db_instances')
        for page in paginator.paginate():
            for db_instance in page.get('DBInstances', []):
                capture_time = db_instance.get('InstanceCreateTime', datetime.utcnow()).isoformat()
                store_data_in_dynamodb(
                    resource_id=db_instance['DBInstanceIdentifier'],
                    resource_type='AWS::RDS::DBInstance',
                    configuration=db_instance,
                    region=region,
                    configuration_capture_time=capture_time
                )
    except Exception as e:
        logging.error(f"Error collecting RDS data in region {region}: {e}")

def collect_lambda_data(region):
    """Collect Lambda functions."""
    logging.info(f"Collecting Lambda data in region: {region}")
    try:
        lambda_client = boto3.client('lambda', region_name=region)
        paginator = lambda_client.get_paginator('list_functions')
        for page in paginator.paginate():
            for function in page.get('Functions', []):
                capture_time = function.get('LastModified', datetime.utcnow()).isoformat()
                store_data_in_dynamodb(
                    resource_id=function['FunctionArn'],
                    resource_type='AWS::Lambda::Function',
                    configuration=function,
                    region=region,
                    configuration_capture_time=capture_time
                )
    except Exception as e:
        logging.error(f"Error collecting Lambda data in region {region}: {e}")

def collect_cloudformation_data(region):
    """Collect CloudFormation stacks."""
    logging.info(f"Collecting CloudFormation data in region: {region}")
    try:
        cf = boto3.client('cloudformation', region_name=region)
        paginator = cf.get_paginator('describe_stacks')
        for page in paginator.paginate():
            for stack in page.get('Stacks', []):
                capture_time = stack.get('CreationTime', datetime.utcnow()).isoformat()
                store_data_in_dynamodb(
                    resource_id=stack['StackId'],
                    resource_type='AWS::CloudFormation::Stack',
                    configuration=stack,
                    region=region,
                    configuration_capture_time=capture_time
                )
    except Exception as e:
        logging.error(f"Error collecting CloudFormation data in region {region}: {e}")

def collect_elb_data(region):
    """Collect Classic ELBs and ELBv2 load balancers."""
    logging.info(f"Collecting ELB data in region: {region}")
    try:
        # Classic ELBs
        elb = boto3.client('elb', region_name=region)
        paginator = elb.get_paginator('describe_load_balancers')
        for page in paginator.paginate():
            for lb in page.get('LoadBalancerDescriptions', []):
                capture_time = lb.get('CreatedTime', datetime.utcnow()).isoformat()
                store_data_in_dynamodb(
                    resource_id=lb['LoadBalancerName'],
                    resource_type='AWS::ElasticLoadBalancing::LoadBalancer',
                    configuration=lb,
                    region=region,
                    configuration_capture_time=capture_time
                )
        # ELBv2
        elbv2 = boto3.client('elbv2', region_name=region)
        paginator = elbv2.get_paginator('describe_load_balancers')
        for page in paginator.paginate():
            for lb in page.get('LoadBalancers', []):
                capture_time = lb.get('CreatedTime', datetime.utcnow()).isoformat()
                store_data_in_dynamodb(
                    resource_id=lb['LoadBalancerArn'],
                    resource_type='AWS::ElasticLoadBalancingV2::LoadBalancer',
                    configuration=lb,
                    region=region,
                    configuration_capture_time=capture_time
                )
    except Exception as e:
        logging.error(f"Error collecting ELB data in region {region}: {e}")

def collect_cloudwatch_data(region):
    """Collect CloudWatch alarms, metrics, and dashboards."""
    logging.info(f"Collecting CloudWatch data in region: {region}")
    try:
        cw = boto3.client('cloudwatch', region_name=region)
        # Alarms
        paginator = cw.get_paginator('describe_alarms')
        for page in paginator.paginate():
            for alarm in page.get('MetricAlarms', []):
                capture_time = alarm.get('StateUpdatedTimestamp', datetime.utcnow()).isoformat()
                store_data_in_dynamodb(
                    resource_id=alarm['AlarmArn'],
                    resource_type='AWS::CloudWatch::Alarm',
                    configuration=alarm,
                    region=region,
                    configuration_capture_time=capture_time
                )
        # Metrics
        paginator = cw.get_paginator('list_metrics')
        for page in paginator.paginate():
            for metric in page.get('Metrics', []):
                metric_id = f"{metric['Namespace']}:{metric['MetricName']}:" + ",".join([f"{dim['Name']}={dim['Value']}" for dim in metric.get('Dimensions', [])])
                capture_time = datetime.utcnow().isoformat()
                store_data_in_dynamodb(
                    resource_id=metric_id,
                    resource_type='AWS::CloudWatch::Metric',
                    configuration=metric,
                    region=region,
                    configuration_capture_time=capture_time
                )
        # Dashboards
        paginator = cw.get_paginator('list_dashboards')
        for page in paginator.paginate():
            for dashboard in page.get('DashboardEntries', []):
                dashboard_name = dashboard['DashboardName']
                try:
                    dashboard_content = cw.get_dashboard(DashboardName=dashboard_name)['DashboardBody']
                except cw.exceptions.ResourceNotFound:
                    dashboard_content = None
                capture_time = datetime.utcnow().isoformat()
                dashboard_info = {
                    'DashboardName': dashboard_name,
                    'DashboardBody': dashboard_content
                }
                store_data_in_dynamodb(
                    resource_id=dashboard_name,
                    resource_type='AWS::CloudWatch::Dashboard',
                    configuration=dashboard_info,
                    region=region,
                    configuration_capture_time=capture_time
                )
    except Exception as e:
        logging.error(f"Error collecting CloudWatch data in region {region}: {e}")

def collect_eks_data(region):
    """Collect EKS clusters and node groups."""
    logging.info(f"Collecting EKS data in region: {region}")
    try:
        eks = boto3.client('eks', region_name=region)
        # Clusters
        paginator = eks.get_paginator('list_clusters')
        for page in paginator.paginate():
            for cluster_name in page.get('clusters', []):
                try:
                    cluster_info = eks.describe_cluster(name=cluster_name)['cluster']
                    cluster_capture_time = cluster_info.get('createdAt', datetime.utcnow()).isoformat()
                    store_data_in_dynamodb(
                        resource_id=cluster_info['arn'],
                        resource_type='AWS::EKS::Cluster',
                        configuration=cluster_info,
                        region=region,
                        configuration_capture_time=cluster_capture_time
                    )
                    # Node Groups
                    paginator_ng = eks.get_paginator('list_nodegroups')
                    for page_ng in paginator_ng.paginate(clusterName=cluster_name):
                        for nodegroup_name in page_ng.get('nodegroups', []):
                            try:
                                nodegroup_info = eks.describe_nodegroup(clusterName=cluster_name, nodegroupName=nodegroup_name)['nodegroup']
                                nodegroup_capture_time = nodegroup_info.get('createdAt', datetime.utcnow()).isoformat()
                                store_data_in_dynamodb(
                                    resource_id=nodegroup_info['arn'],
                                    resource_type='AWS::EKS::NodeGroup',
                                    configuration=nodegroup_info,
                                    region=region,
                                    configuration_capture_time=nodegroup_capture_time
                                )
                            except ClientError as e:
                                logging.error(f"Error describing node group {nodegroup_name} in cluster {cluster_name}: {e}")
                except ClientError as e:
                    logging.error(f"Error describing cluster {cluster_name} in region {region}: {e}")
    except Exception as e:
        logging.error(f"Error collecting EKS data in region {region}: {e}")

def collect_cloudwatch_logs_data(region):
    """Collect CloudWatch Log Groups."""
    logging.info(f"Collecting CloudWatch Logs data in region: {region}")
    try:
        logs = boto3.client('logs', region_name=region)
        paginator = logs.get_paginator('describe_log_groups')
        for page in paginator.paginate():
            for log_group in page.get('logGroups', []):
                capture_time = log_group.get('creationTime', datetime.utcnow().isoformat())
                store_data_in_dynamodb(
                    resource_id=log_group['logGroupName'],
                    resource_type='AWS::Logs::LogGroup',
                    configuration=log_group,
                    region=region,
                    configuration_capture_time=capture_time
                )
    except Exception as e:
        logging.error(f"Error collecting CloudWatch Logs data in region {region}: {e}")

def collect_service_data_regionally(collect_function, service_name):
    """Collect data for a service across all regions."""
    regions = get_all_regions()
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(collect_function, region) for region in regions]
        for future in futures:
            try:
                future.result()
            except Exception as e:
                logging.error(f"Error collecting data for service {service_name}: {e}")

def collect_all_data():
    """Collect data from all services."""
    logging.info("Starting initial data collection...")
    # Ensure the DynamoDB table exists
    try:
        table = dynamodb_resource.Table(DYNAMODB_TABLE_NAME)
        table.load()
        logging.info(f"DynamoDB table '{DYNAMODB_TABLE_NAME}' exists.")
    except dynamodb_resource.meta.client.exceptions.ResourceNotFoundException:
        logging.error(f"DynamoDB table '{DYNAMODB_TABLE_NAME}' does not exist.")
        exit(1)

    # Ensure the S3 bucket exists
    try:
        s3_client.head_bucket(Bucket=S3_BUCKET_NAME)
        logging.info(f"S3 bucket '{S3_BUCKET_NAME}' exists.")
    except ClientError:
        logging.error(f"S3 bucket '{S3_BUCKET_NAME}' does not exist or is inaccessible.")
        exit(1)

    # Collect data from services
    services = [
        (collect_ec2_data, 'EC2'),
        (collect_s3_data, 'S3'),
        (collect_iam_data, 'IAM'),
        (collect_rds_data, 'RDS'),
        (collect_lambda_data, 'Lambda'),
        (collect_cloudformation_data, 'CloudFormation'),
        (collect_elb_data, 'ELB'),
        (collect_cloudwatch_data, 'CloudWatch'),
        (collect_eks_data, 'EKS'),
        (collect_cloudwatch_logs_data, 'CloudWatchLogs')
    ]

    for collect_function, service_name in services:
        if service_name in ['S3', 'IAM']:  # Services that are global or don't require regional iteration
            try:
                if service_name == 'S3':
                    collect_function()
                elif service_name == 'IAM':
                    collect_function()
            except Exception as e:
                logging.error(f"Error collecting data for service {service_name}: {e}")
        else:
            collect_service_data_regionally(collect_function, service_name)

    logging.info("Initial data collection completed.")

def process_config_notifications():
    """Process AWS Config notifications from the SQS queue."""
    logging.info("Starting to process AWS Config notifications...")
    while True:
        try:
            response = sqs_client.receive_message(
                QueueUrl=SQS_QUEUE_URL,
                MaxNumberOfMessages=10,
                WaitTimeSeconds=20  # Long polling
            )
            messages = response.get('Messages', [])
            if not messages:
                continue
            for message in messages:
                try:
                    body = json.loads(message['Body'])
                    sns_message = json.loads(body['Message'])
                    message_type = sns_message.get('messageType')
                    if message_type == 'ConfigurationItemChangeNotification':
                        configuration_item = sns_message.get('configurationItem')
                        process_configuration_item(configuration_item)
                    elif message_type == 'OversizedConfigurationItemChangeNotification':
                        configuration_snapshot = sns_message.get('configurationItemSummary')
                        get_and_process_full_configuration_item(configuration_snapshot)
                    else:
                        logging.warning(f"Received unsupported message type: {message_type}")

                    # Delete the message from the queue
                    sqs_client.delete_message(
                        QueueUrl=SQS_QUEUE_URL,
                        ReceiptHandle=message['ReceiptHandle']
                    )
                except json.JSONDecodeError as e:
                    logging.error(f"Error decoding JSON message: {e}")
                except Exception as e:
                    logging.error(f"Error processing message: {e}")
        except Exception as e:
            logging.error(f"Error receiving messages from SQS: {e}")
            time.sleep(5)  # Wait before retrying

def process_configuration_item(configuration_item):
    """Process a configuration item and update the DynamoDB table."""
    try:
        resource_id = configuration_item['resourceId']
        resource_type = configuration_item['resourceType']
        region = configuration_item['awsRegion']
        configuration = configuration_item.get('configuration', {})
        tags = configuration_item.get('tags', {})
        configuration_capture_time = configuration_item.get('configurationItemCaptureTime', datetime.utcnow().isoformat())
        
        if not configuration:
            # Fetch the full configuration if not provided
            configuration = get_resource_configuration(resource_id, resource_type, region)
        
        if configuration:
            store_data_in_dynamodb(
                resource_id,
                resource_type,
                configuration,
                region,
                configuration_capture_time,
                tags
            )
        else:
            logging.warning(f"No configuration data available for resource {resource_id}.")
    except KeyError as e:
        logging.error(f"Missing key in configuration item: {e}")
    except Exception as e:
        logging.error(f"Error processing configuration item: {e}")

def get_and_process_full_configuration_item(configuration_snapshot):
    """Retrieve and process the full configuration item for oversized notifications."""
    try:
        config_client = boto3.client('config', region_name=AGGREGATOR_REGION)
        response = config_client.get_aggregate_resource_config(
            ConfigurationAggregatorName=AGGREGATOR_NAME,
            ResourceIdentifier={
                'SourceAccountId': configuration_snapshot['accountId'],
                'SourceRegion': configuration_snapshot['awsRegion'],
                'ResourceId': configuration_snapshot['resourceId'],
                'ResourceType': configuration_snapshot['resourceType']
            }
        )
        configuration_item = json.loads(response['ConfigurationItem'])
        process_configuration_item(configuration_item)
    except ClientError as e:
        logging.error(f"Error retrieving full configuration for resource {configuration_snapshot['resourceId']}: {e}")
    except json.JSONDecodeError as e:
        logging.error(f"Error decoding ConfigurationItem JSON: {e}")
    except Exception as e:
        logging.error(f"Unexpected error: {e}")

def get_resource_configuration(resource_id, resource_type, region):
    """Fetch the current configuration of a resource."""
    try:
        if resource_type == 'AWS::EC2::Instance':
            ec2 = boto3.client('ec2', region_name=region)
            response = ec2.describe_instances(InstanceIds=[resource_id])
            instances = response.get('Reservations', [])[0].get('Instances', [])
            return instances[0] if instances else None
        elif resource_type == 'AWS::S3::Bucket':
            s3 = boto3.client('s3', region_name=region)
            bucket_location = s3.get_bucket_location(Bucket=resource_id)
            bucket_info = {'BucketName': resource_id, 'Region': bucket_location.get('LocationConstraint')}
            # Add additional bucket details as needed
            return bucket_info
        elif resource_type == 'AWS::IAM::User':
            iam = boto3.client('iam')
            user_detail = iam.get_user(UserName=resource_id)['User']
            return user_detail
        elif resource_type == 'AWS::Lambda::Function':
            lambda_client = boto3.client('lambda', region_name=region)
            function = lambda_client.get_function(FunctionName=resource_id)['Configuration']
            return function
        elif resource_type == 'AWS::RDS::DBInstance':
            rds = boto3.client('rds', region_name=region)
            response = rds.describe_db_instances(DBInstanceIdentifier=resource_id)
            db_instances = response.get('DBInstances', [])
            return db_instances[0] if db_instances else None
        elif resource_type == 'AWS::EKS::Cluster':
            eks = boto3.client('eks', region_name=region)
            response = eks.describe_cluster(name=resource_id)
            return response['cluster']
        elif resource_type == 'AWS::EKS::NodeGroup':
            eks = boto3.client('eks', region_name=region)
            cluster_name = find_cluster_name_by_nodegroup(resource_id)
            if not cluster_name:
                logging.warning(f"Cluster not found for NodeGroup {resource_id}")
                return None
            response = eks.describe_nodegroup(clusterName=cluster_name, nodegroupName=resource_id)
            return response['nodegroup']
        elif resource_type == 'AWS::Logs::LogGroup':
            logs = boto3.client('logs', region_name=region)
            response = logs.describe_log_groups(logGroupNamePrefix=resource_id)
            log_groups = response.get('logGroups', [])
            return log_groups[0] if log_groups else None
        elif resource_type == 'AWS::CloudWatch::Metric':
            # Metrics are typically retrieved via list_metrics and don't have a single identifier
            # Returning basic info as example
            return {'Metric': resource_id}
        elif resource_type == 'AWS::CloudWatch::Dashboard':
            cw = boto3.client('cloudwatch', region_name=region)
            try:
                response = cw.get_dashboard(DashboardName=resource_id)
                return {'DashboardName': resource_id, 'DashboardBody': response.get('DashboardBody')}
            except cw.exceptions.ResourceNotFound:
                return None
        else:
            logging.warning(f"Fetching configuration for resource type {resource_type} is not implemented.")
            return None
    except ClientError as e:
        logging.error(f"Error fetching configuration for resource {resource_id}: {e}")
        return None
    except Exception as e:
        logging.error(f"Unexpected error fetching configuration for resource {resource_id}: {e}")
        return None

def find_cluster_name_by_nodegroup(nodegroup_arn):
    """
    Find the cluster name associated with a given nodegroup ARN.
    This function iterates through all EKS clusters to find the matching nodegroup.
    """
    logging.info(f"Finding cluster for nodegroup ARN: {nodegroup_arn}")
    try:
        eks = boto3.client('eks', region_name=AGGREGATOR_REGION)
        paginator = eks.get_paginator('list_clusters')
        for page in paginator.paginate():
            for cluster_name in page.get('clusters', []):
                paginator_ng = eks.get_paginator('list_nodegroups')
                for page_ng in paginator_ng.paginate(clusterName=cluster_name):
                    for nodegroup_name in page_ng.get('nodegroups', []):
                        try:
                            nodegroup_info = eks.describe_nodegroup(clusterName=cluster_name, nodegroupName=nodegroup_name)['nodegroup']
                            if nodegroup_info['arn'] == nodegroup_arn:
                                logging.info(f"Found cluster '{cluster_name}' for nodegroup ARN '{nodegroup_arn}'")
                                return cluster_name
                        except ClientError as e:
                            logging.error(f"Error describing node group {nodegroup_name} in cluster {cluster_name}: {e}")
        logging.warning(f"No cluster found for nodegroup ARN {nodegroup_arn}")
        return None
    except ClientError as e:
        logging.error(f"Error listing EKS clusters: {e}")
        return None
    except Exception as e:
        logging.error(f"Unexpected error finding cluster for nodegroup {nodegroup_arn}: {e}")
        return None

def collect_cloudwatch_logs_data(region):
    """Collect CloudWatch Log Groups."""
    logging.info(f"Collecting CloudWatch Logs data in region: {region}")
    try:
        logs = boto3.client('logs', region_name=region)
        paginator = logs.get_paginator('describe_log_groups')
        for page in paginator.paginate():
            for log_group in page.get('logGroups', []):
                capture_time = log_group.get('creationTime', datetime.utcnow()).isoformat()
                store_data_in_dynamodb(
                    resource_id=log_group['logGroupName'],
                    resource_type='AWS::Logs::LogGroup',
                    configuration=log_group,
                    region=region,
                    configuration_capture_time=capture_time
                )
    except Exception as e:
        logging.error(f"Error collecting CloudWatch Logs data in region {region}: {e}")

def collect_service_data_regionally(collect_function, service_name):
    """Collect data for a service across all regions."""
    regions = get_all_regions()
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(collect_function, region) for region in regions]
        for future in futures:
            try:
                future.result()
            except Exception as e:
                logging.error(f"Error collecting data for service {service_name}: {e}")

def collect_all_data():
    """Collect data from all services."""
    logging.info("Starting initial data collection...")
    # Ensure the DynamoDB table exists
    try:
        table = dynamodb_resource.Table(DYNAMODB_TABLE_NAME)
        table.load()
        logging.info(f"DynamoDB table '{DYNAMODB_TABLE_NAME}' exists.")
    except dynamodb_resource.meta.client.exceptions.ResourceNotFoundException:
        logging.error(f"DynamoDB table '{DYNAMODB_TABLE_NAME}' does not exist.")
        exit(1)

    # Ensure the S3 bucket exists
    try:
        s3_client.head_bucket(Bucket=S3_BUCKET_NAME)
        logging.info(f"S3 bucket '{S3_BUCKET_NAME}' exists.")
    except ClientError:
        logging.error(f"S3 bucket '{S3_BUCKET_NAME}' does not exist or is inaccessible.")
        exit(1)

    # Collect data from services
    services = [
        (collect_ec2_data, 'EC2'),
        (collect_s3_data, 'S3'),
        (collect_iam_data, 'IAM'),
        (collect_rds_data, 'RDS'),
        (collect_lambda_data, 'Lambda'),
        (collect_cloudformation_data, 'CloudFormation'),
        (collect_elb_data, 'ELB'),
        (collect_cloudwatch_data, 'CloudWatch'),
        (collect_eks_data, 'EKS'),
        (collect_cloudwatch_logs_data, 'CloudWatchLogs')
    ]

    for collect_function, service_name in services:
        if service_name in ['S3', 'IAM']:  # Services that are global or don't require regional iteration
            try:
                if service_name == 'S3':
                    collect_function()
                elif service_name == 'IAM':
                    collect_function()
            except Exception as e:
                logging.error(f"Error collecting data for service {service_name}: {e}")
        else:
            collect_service_data_regionally(collect_function, service_name)

    logging.info("Initial data collection completed.")

def process_config_notifications():
    """Process AWS Config notifications from the SQS queue."""
    logging.info("Starting to process AWS Config notifications...")
    while True:
        try:
            response = sqs_client.receive_message(
                QueueUrl=SQS_QUEUE_URL,
                MaxNumberOfMessages=10,
                WaitTimeSeconds=20  # Long polling
            )
            messages = response.get('Messages', [])
            if not messages:
                continue
            for message in messages:
                try:
                    body = json.loads(message['Body'])
                    sns_message = json.loads(body['Message'])
                    message_type = sns_message.get('messageType')
                    if message_type == 'ConfigurationItemChangeNotification':
                        configuration_item = sns_message.get('configurationItem')
                        process_configuration_item(configuration_item)
                    elif message_type == 'OversizedConfigurationItemChangeNotification':
                        configuration_snapshot = sns_message.get('configurationItemSummary')
                        get_and_process_full_configuration_item(configuration_snapshot)
                    else:
                        logging.warning(f"Received unsupported message type: {message_type}")

                    # Delete the message from the queue
                    sqs_client.delete_message(
                        QueueUrl=SQS_QUEUE_URL,
                        ReceiptHandle=message['ReceiptHandle']
                    )
                except json.JSONDecodeError as e:
                    logging.error(f"Error decoding JSON message: {e}")
                except Exception as e:
                    logging.error(f"Error processing message: {e}")
        except Exception as e:
            logging.error(f"Error receiving messages from SQS: {e}")
            time.sleep(5)  # Wait before retrying

def process_configuration_item(configuration_item):
    """Process a configuration item and update the DynamoDB table."""
    try:
        resource_id = configuration_item['resourceId']
        resource_type = configuration_item['resourceType']
        region = configuration_item['awsRegion']
        configuration = configuration_item.get('configuration', {})
        tags = configuration_item.get('tags', {})
        configuration_capture_time = configuration_item.get('configurationItemCaptureTime', datetime.utcnow().isoformat())
        
        if not configuration:
            # Fetch the full configuration if not provided
            configuration = get_resource_configuration(resource_id, resource_type, region)
        
        if configuration:
            store_data_in_dynamodb(
                resource_id,
                resource_type,
                configuration,
                region,
                configuration_capture_time,
                tags
            )
        else:
            logging.warning(f"No configuration data available for resource {resource_id}.")
    except KeyError as e:
        logging.error(f"Missing key in configuration item: {e}")
    except Exception as e:
        logging.error(f"Error processing configuration item: {e}")

def get_and_process_full_configuration_item(configuration_snapshot):
    """Retrieve and process the full configuration item for oversized notifications."""
    try:
        config_client = boto3.client('config', region_name=AGGREGATOR_REGION)
        response = config_client.get_aggregate_resource_config(
            ConfigurationAggregatorName=AGGREGATOR_NAME,
            ResourceIdentifier={
                'SourceAccountId': configuration_snapshot['accountId'],
                'SourceRegion': configuration_snapshot['awsRegion'],
                'ResourceId': configuration_snapshot['resourceId'],
                'ResourceType': configuration_snapshot['resourceType']
            }
        )
        configuration_item = json.loads(response['ConfigurationItem'])
        process_configuration_item(configuration_item)
    except ClientError as e:
        logging.error(f"Error retrieving full configuration for resource {configuration_snapshot['resourceId']}: {e}")
    except json.JSONDecodeError as e:
        logging.error(f"Error decoding ConfigurationItem JSON: {e}")
    except Exception as e:
        logging.error(f"Unexpected error: {e}")

def get_resource_configuration(resource_id, resource_type, region):
    """Fetch the current configuration of a resource."""
    try:
        if resource_type == 'AWS::EC2::Instance':
            ec2 = boto3.client('ec2', region_name=region)
            response = ec2.describe_instances(InstanceIds=[resource_id])
            instances = response.get('Reservations', [])[0].get('Instances', [])
            return instances[0] if instances else None
        elif resource_type == 'AWS::S3::Bucket':
            s3 = boto3.client('s3', region_name=region)
            bucket_location = s3.get_bucket_location(Bucket=resource_id)
            bucket_info = {'BucketName': resource_id, 'Region': bucket_location.get('LocationConstraint')}
            # Add additional bucket details as needed
            return bucket_info
        elif resource_type == 'AWS::IAM::User':
            iam = boto3.client('iam')
            user_detail = iam.get_user(UserName=resource_id)['User']
            return user_detail
        elif resource_type == 'AWS::Lambda::Function':
            lambda_client = boto3.client('lambda', region_name=region)
            function = lambda_client.get_function(FunctionName=resource_id)['Configuration']
            return function
        elif resource_type == 'AWS::RDS::DBInstance':
            rds = boto3.client('rds', region_name=region)
            response = rds.describe_db_instances(DBInstanceIdentifier=resource_id)
            db_instances = response.get('DBInstances', [])
            return db_instances[0] if db_instances else None
        elif resource_type == 'AWS::EKS::Cluster':
            eks = boto3.client('eks', region_name=region)
            response = eks.describe_cluster(name=resource_id)
            return response['cluster']
        elif resource_type == 'AWS::EKS::NodeGroup':
            eks = boto3.client('eks', region_name=region)
            cluster_name = find_cluster_name_by_nodegroup(resource_id)
            if not cluster_name:
                logging.warning(f"Cluster not found for NodeGroup {resource_id}")
                return None
            response = eks.describe_nodegroup(clusterName=cluster_name, nodegroupName=resource_id)
            return response['nodegroup']
        elif resource_type == 'AWS::Logs::LogGroup':
            logs = boto3.client('logs', region_name=region)
            response = logs.describe_log_groups(logGroupNamePrefix=resource_id)
            log_groups = response.get('logGroups', [])
            return log_groups[0] if log_groups else None
        elif resource_type == 'AWS::CloudWatch::Metric':
            # Metrics are typically retrieved via list_metrics and don't have a single identifier
            # Returning basic info as example
            return {'Metric': resource_id}
        elif resource_type == 'AWS::CloudWatch::Dashboard':
            cw = boto3.client('cloudwatch', region_name=region)
            try:
                response = cw.get_dashboard(DashboardName=resource_id)
                return {'DashboardName': resource_id, 'DashboardBody': response.get('DashboardBody')}
            except cw.exceptions.ResourceNotFound:
                return None
        else:
            logging.warning(f"Fetching configuration for resource type {resource_type} is not implemented.")
            return None
    except ClientError as e:
        logging.error(f"Error fetching configuration for resource {resource_id}: {e}")
        return None
    except Exception as e:
        logging.error(f"Unexpected error fetching configuration for resource {resource_id}: {e}")
        return None

def find_cluster_name_by_nodegroup(nodegroup_arn):
    """
    Find the cluster name associated with a given nodegroup ARN.
    This function iterates through all EKS clusters to find the matching nodegroup.
    """
    logging.info(f"Finding cluster for nodegroup ARN: {nodegroup_arn}")
    try:
        eks = boto3.client('eks', region_name=AGGREGATOR_REGION)
        paginator = eks.get_paginator('list_clusters')
        for page in paginator.paginate():
            for cluster_name in page.get('clusters', []):
                paginator_ng = eks.get_paginator('list_nodegroups')
                for page_ng in paginator_ng.paginate(clusterName=cluster_name):
                    for nodegroup_name in page_ng.get('nodegroups', []):
                        try:
                            nodegroup_info = eks.describe_nodegroup(clusterName=cluster_name, nodegroupName=nodegroup_name)['nodegroup']
                            if nodegroup_info['arn'] == nodegroup_arn:
                                logging.info(f"Found cluster '{cluster_name}' for nodegroup ARN '{nodegroup_arn}'")
                                return cluster_name
                        except ClientError as e:
                            logging.error(f"Error describing node group {nodegroup_name} in cluster {cluster_name}: {e}")
        logging.warning(f"No cluster found for nodegroup ARN {nodegroup_arn}")
        return None
    except ClientError as e:
        logging.error(f"Error listing EKS clusters: {e}")
        return None
    except Exception as e:
        logging.error(f"Unexpected error finding cluster for nodegroup {nodegroup_arn}: {e}")
        return None

def collect_cloudwatch_logs_data(region):
    """Collect CloudWatch Log Groups."""
    logging.info(f"Collecting CloudWatch Logs data in region: {region}")
    try:
        logs = boto3.client('logs', region_name=region)
        paginator = logs.get_paginator('describe_log_groups')
        for page in paginator.paginate():
            for log_group in page.get('logGroups', []):
                capture_time = log_group.get('creationTime', datetime.utcnow()).isoformat()
                store_data_in_dynamodb(
                    resource_id=log_group['logGroupName'],
                    resource_type='AWS::Logs::LogGroup',
                    configuration=log_group,
                    region=region,
                    configuration_capture_time=capture_time
                )
    except Exception as e:
        logging.error(f"Error collecting CloudWatch Logs data in region {region}: {e}")

def collect_service_data_regionally(collect_function, service_name):
    """Collect data for a service across all regions."""
    regions = get_all_regions()
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(collect_function, region) for region in regions]
        for future in futures:
            try:
                future.result()
            except Exception as e:
                logging.error(f"Error collecting data for service {service_name}: {e}")

def collect_all_data():
    """Collect data from all services."""
    logging.info("Starting initial data collection...")
    # Ensure the DynamoDB table exists
    try:
        table = dynamodb_resource.Table(DYNAMODB_TABLE_NAME)
        table.load()
        logging.info(f"DynamoDB table '{DYNAMODB_TABLE_NAME}' exists.")
    except dynamodb_resource.meta.client.exceptions.ResourceNotFoundException:
        logging.error(f"DynamoDB table '{DYNAMODB_TABLE_NAME}' does not exist.")
        exit(1)

    # Ensure the S3 bucket exists
    try:
        s3_client.head_bucket(Bucket=S3_BUCKET_NAME)
        logging.info(f"S3 bucket '{S3_BUCKET_NAME}' exists.")
    except ClientError:
        logging.error(f"S3 bucket '{S3_BUCKET_NAME}' does not exist or is inaccessible.")
        exit(1)

    # Collect data from services
    services = [
        (collect_ec2_data, 'EC2'),
        (collect_s3_data, 'S3'),
        (collect_iam_data, 'IAM'),
        (collect_rds_data, 'RDS'),
        (collect_lambda_data, 'Lambda'),
        (collect_cloudformation_data, 'CloudFormation'),
        (collect_elb_data, 'ELB'),
        (collect_cloudwatch_data, 'CloudWatch'),
        (collect_eks_data, 'EKS'),
        (collect_cloudwatch_logs_data, 'CloudWatchLogs')
    ]

    for collect_function, service_name in services:
        if service_name in ['S3', 'IAM']:  # Services that are global or don't require regional iteration
            try:
                if service_name == 'S3':
                    collect_function()
                elif service_name == 'IAM':
                    collect_function()
            except Exception as e:
                logging.error(f"Error collecting data for service {service_name}: {e}")
        else:
            collect_service_data_regionally(collect_function, service_name)

    logging.info("Initial data collection completed.")

# Inside your data_collector.py

def process_config_notifications():
    """Process AWS Config notifications from the SQS queue."""
    logging.info("Starting to process AWS Config notifications...")
    while True:
        try:
            response = sqs_client.receive_message(
                QueueUrl=SQS_QUEUE_URL,
                MaxNumberOfMessages=10,
                WaitTimeSeconds=20  # Long polling
            )
            messages = response.get('Messages', [])
            if not messages:
                continue
            for message in messages:
                try:
                    logging.info(f"Received message: {message['MessageId']}")
                    body = json.loads(message['Body'])
                    sns_message = json.loads(body['Message'])
                    message_type = sns_message.get('messageType')
                    logging.info(f"Processing message type: {message_type}")
                    if message_type == 'ConfigurationItemChangeNotification':
                        configuration_item = sns_message.get('configurationItem')
                        process_configuration_item(configuration_item)
                    elif message_type == 'OversizedConfigurationItemChangeNotification':
                        configuration_snapshot = sns_message.get('configurationItemSummary')
                        get_and_process_full_configuration_item(configuration_snapshot)
                    else:
                        logging.warning(f"Received unsupported message type: {message_type}")

                    # Delete the message from the queue
                    sqs_client.delete_message(
                        QueueUrl=SQS_QUEUE_URL,
                        ReceiptHandle=message['ReceiptHandle']
                    )
                    logging.info(f"Deleted message: {message['MessageId']}")
                except json.JSONDecodeError as e:
                    logging.error(f"Error decoding JSON message: {e}")
                except Exception as e:
                    logging.error(f"Error processing message: {e}")
        except Exception as e:
            logging.error(f"Error receiving messages from SQS: {e}")
            time.sleep(5)  # Wait before retrying

def main():
    try:
        # Step 1: Perform initial data collection
        collect_all_data()
    
        # Step 2: Start processing AWS Config notifications
        process_config_notifications()
    except KeyboardInterrupt:
        logging.info("Data collector stopped by user.")
    except Exception as e:
        logging.error(f"Unexpected error in main: {e}")

if __name__ == '__main__':
    main()
