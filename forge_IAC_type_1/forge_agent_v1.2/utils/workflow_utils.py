from concurrent.futures import ThreadPoolExecutor, as_completed
from botocore.exceptions import ClientError
import boto3
import os
import json
import concurrent.futures
from dotenv import load_dotenv
from pydantic import BaseModel, Field
load_dotenv()
import sys


# old_stdout = sys.stdout
# sys.stdout = open(os.devnull, "w")

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

def configure_aws_from_env(state):
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

    boto3.setup_default_session(
        aws_access_key_id=aws_access_key_id,
        aws_secret_access_key=aws_secret_access_key,
        region_name=aws_default_region
    )

    # Optionally, verify configuration by calling STS GetCallerIdentity
    client = boto3.client("sts")
    identity = client.get_caller_identity()

    state["aws_identity"] = identity["Arn"]

    return state

# Define the directory where data will be saved
DATA_DIR = os.environ.get("RAG_DATABASE_PATH") + "/AWS_DATA"

def get_session(region_name):
    return boto3.session.Session(region_name=region_name)

def list_accessible_regions(service_name):
    session = boto3.session.Session()
    regions = session.get_available_regions(service_name)
    excluded_regions = ['cn-north-1', 'cn-northwest-1', 'us-gov-west-1', 'us-gov-east-1']
    regions = [region for region in regions if region not in excluded_regions]
    accessible_regions = []
    for region in regions:
        try:
            client = get_session(region).client(service_name)
            # Test the service in the region
            if service_name == 'ec2':
                client.describe_account_attributes()
            elif service_name == 'rds':
                client.describe_db_instances()
            elif service_name == 'lambda':
                client.list_functions(MaxItems=1)
            elif service_name == 'cloudtrail':
                client.describe_trails()
            elif service_name == 'cloudformation':
                client.list_stacks()
            elif service_name == 'elbv2':
                client.describe_load_balancers()
            elif service_name == 'eks':
                client.list_clusters()
            elif service_name == 'cloudwatch':
                client.describe_alarms()
            elif service_name == 'dynamodb':
                client.list_tables()
            elif service_name == 'sqs':
                client.list_queues()
            elif service_name == 'sns':
                client.list_topics()
            elif service_name == 'elasticfilesystem':
                client.describe_file_systems()
            elif service_name == 'ecs':
                client.list_clusters()
            elif service_name == 'elasticbeanstalk':
                client.describe_applications()
            else:
                pass  # Adjust for other services as needed
            accessible_regions.append(region)
        except ClientError as e:
            error_code = e.response['Error']['Code']
            if error_code == 'AuthFailure':
                print(f"AuthFailure in region {region} for service {service_name}: {e}")
            else:
                print(f"Error accessing service {service_name} in region {region}: {e}")
        except Exception as e:
            print(f"Unexpected error in region {region} for service {service_name}: {e}")
    return accessible_regions

def save_data_to_file(data, filepath):
    if data:
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        try:
            with open(filepath, 'w') as f:
                json.dump(data, f, default=str, indent=2)
            print(f"Data saved to {filepath}")
        except Exception as e:
            print(f"Error saving data to file {filepath}: {e}")
    else:
        print(f"No meaningful data to save for {filepath}")

def collect_ec2_data():
    service_name = 'ec2'
    regions = list_accessible_regions(service_name)
    for region in regions:
        try:
            ec2 = get_session(region).client(service_name)
            instances = ec2.describe_instances()
            has_data = any(reservation.get('Instances') for reservation in instances.get('Reservations', []))
            if has_data:
                filepath = os.path.join(DATA_DIR, f"EC2/{region}_ec2_instances.json")
                save_data_to_file(instances, filepath)
            else:
                print(f"No EC2 instances found in region {region}")
        except ClientError as e:
            print(f"Error fetching EC2 data in region {region}: {e}")

def collect_s3_data():
    s3 = boto3.client('s3')
    try:
        buckets = s3.list_buckets()
        if buckets.get('Buckets'):
            filepath = os.path.join(DATA_DIR, "S3/s3_buckets.json")
            save_data_to_file(buckets, filepath)
        else:
            print("No S3 buckets found")
    except ClientError as e:
        print(f"Error fetching S3 data: {e}")

def collect_vpc_data():
    service_name = 'ec2'
    regions = list_accessible_regions(service_name)
    for region in regions:
        try:
            ec2 = get_session(region).client(service_name)
            vpcs = ec2.describe_vpcs()
            if vpcs.get('Vpcs'):
                filepath = os.path.join(DATA_DIR, f"VPC/{region}_vpcs.json")
                save_data_to_file(vpcs, filepath)
            else:
                print(f"No VPCs found in region {region}")
        except ClientError as e:
            print(f"Error fetching VPC data in region {region}: {e}")

def collect_iam_data():
    iam = boto3.client('iam')
    iam_info = {}
    try:
        users = iam.list_users()
        roles = iam.list_roles()
        policies = iam.list_policies(Scope='Local')
        iam_info['Users'] = users.get('Users', [])
        iam_info['Roles'] = roles.get('Roles', [])
        iam_info['Policies'] = policies.get('Policies', [])
        if any(iam_info.values()):
            filepath = os.path.join(DATA_DIR, "IAM/iam_data.json")
            save_data_to_file(iam_info, filepath)
        else:
            print("No IAM data found")
    except ClientError as e:
        print(f"Error fetching IAM data: {e}")

def collect_rds_data():
    service_name = 'rds'
    regions = list_accessible_regions(service_name)
    for region in regions:
        try:
            rds = get_session(region).client(service_name)
            instances = rds.describe_db_instances()
            if instances.get('DBInstances'):
                filepath = os.path.join(DATA_DIR, f"RDS/{region}_rds_instances.json")
                save_data_to_file(instances, filepath)
            else:
                print(f"No RDS instances found in region {region}")
        except ClientError as e:
            print(f"Error fetching RDS data in region {region}: {e}")

def collect_lambda_data():
    service_name = 'lambda'
    regions = list_accessible_regions(service_name)
    for region in regions:
        try:
            lambda_client = get_session(region).client(service_name)
            functions = lambda_client.list_functions()
            if functions.get('Functions'):
                filepath = os.path.join(DATA_DIR, f"Lambda/{region}_lambda_functions.json")
                save_data_to_file(functions, filepath)
            else:
                print(f"No Lambda functions found in region {region}")
        except ClientError as e:
            print(f"Error fetching Lambda data in region {region}: {e}")

def collect_cloudformation_data():
    service_name = 'cloudformation'
    regions = list_accessible_regions(service_name)
    for region in regions:
        try:
            cf = get_session(region).client(service_name)
            stacks = cf.describe_stacks()
            if stacks.get('Stacks'):
                filepath = os.path.join(DATA_DIR, f"CloudFormation/{region}_stacks.json")
                save_data_to_file(stacks, filepath)
            else:
                print(f"No CloudFormation stacks found in region {region}")
        except ClientError as e:
            print(f"Error fetching CloudFormation data in region {region}: {e}")

def collect_elb_data():
    service_name = 'elbv2'
    regions = list_accessible_regions(service_name)
    for region in regions:
        try:
            elbv2 = get_session(region).client(service_name)
            load_balancers = elbv2.describe_load_balancers()
            if load_balancers.get('LoadBalancers'):
                filepath = os.path.join(DATA_DIR, f"ELB/{region}_load_balancers.json")
                save_data_to_file(load_balancers, filepath)
            else:
                print(f"No Load Balancers found in region {region}")
        except ClientError as e:
            print(f"Error fetching ELB data in region {region}: {e}")

def collect_cloudwatch_data():
    service_name = 'cloudwatch'
    regions = list_accessible_regions(service_name)
    for region in regions:
        try:
            cw = get_session(region).client(service_name)
            alarms = cw.describe_alarms()
            if alarms.get('MetricAlarms') or alarms.get('CompositeAlarms'):
                filepath = os.path.join(DATA_DIR, f"CloudWatch/{region}_alarms.json")
                save_data_to_file(alarms, filepath)
            else:
                print(f"No CloudWatch alarms found in region {region}")
        except ClientError as e:
            print(f"Error fetching CloudWatch data in region {region}: {e}")

def collect_eks_data():
    service_name = 'eks'
    regions = list_accessible_regions(service_name)
    for region in regions:
        try:
            eks = get_session(region).client(service_name)
            clusters = eks.list_clusters()
            if clusters.get('clusters'):
                filepath = os.path.join(DATA_DIR, f"EKS/{region}_clusters.json")
                save_data_to_file(clusters, filepath)
            else:
                print(f"No EKS clusters found in region {region}")
        except ClientError as e:
            print(f"Error fetching EKS data in region {region}: {e}")

def collect_cloudtrail_data():
    service_name = 'cloudtrail'
    regions = list_accessible_regions(service_name)
    for region in regions:
        try:
            ct = get_session(region).client(service_name)
            trails = ct.describe_trails()
            if trails.get('trailList'):
                filepath = os.path.join(DATA_DIR, f"CloudTrail/{region}_trails.json")
                save_data_to_file(trails, filepath)
            else:
                print(f"No CloudTrail trails found in region {region}")
        except ClientError as e:
            print(f"Error fetching CloudTrail data in region {region}: {e}")

def collect_dynamodb_data():
    service_name = 'dynamodb'
    regions = list_accessible_regions(service_name)
    for region in regions:
        try:
            dynamodb = get_session(region).client(service_name)
            tables = dynamodb.list_tables()
            if tables.get('TableNames'):
                filepath = os.path.join(DATA_DIR, f"DynamoDB/{region}_tables.json")
                save_data_to_file(tables, filepath)
            else:
                print(f"No DynamoDB tables found in region {region}")
        except ClientError as e:
            print(f"Error fetching DynamoDB data in region {region}: {e}")

def collect_sqs_data():
    service_name = 'sqs'
    regions = list_accessible_regions(service_name)
    for region in regions:
        try:
            sqs = get_session(region).client(service_name)
            queues = sqs.list_queues()
            if queues.get('QueueUrls'):
                filepath = os.path.join(DATA_DIR, f"SQS/{region}_queues.json")
                save_data_to_file(queues, filepath)
            else:
                print(f"No SQS queues found in region {region}")
        except ClientError as e:
            print(f"Error fetching SQS data in region {region}: {e}")

def collect_sns_data():
    service_name = 'sns'
    regions = list_accessible_regions(service_name)
    for region in regions:
        try:
            sns = get_session(region).client(service_name)
            topics = sns.list_topics()
            if topics.get('Topics'):
                filepath = os.path.join(DATA_DIR, f"SNS/{region}_topics.json")
                save_data_to_file(topics, filepath)
            else:
                print(f"No SNS topics found in region {region}")
        except ClientError as e:
            print(f"Error fetching SNS data in region {region}: {e}")

def collect_efs_data():
    service_name = 'efs'
    regions = list_accessible_regions('elasticfilesystem')
    for region in regions:
        try:
            efs = get_session(region).client('elasticfilesystem')
            file_systems = efs.describe_file_systems()
            if file_systems.get('FileSystems'):
                filepath = os.path.join(DATA_DIR, f"EFS/{region}_file_systems.json")
                save_data_to_file(file_systems, filepath)
            else:
                print(f"No EFS file systems found in region {region}")
        except ClientError as e:
            print(f"Error fetching EFS data in region {region}: {e}")

def collect_cloudfront_data():
    cloudfront = boto3.client('cloudfront')
    try:
        distributions = cloudfront.list_distributions()
        if distributions.get('DistributionList', {}).get('Items'):
            filepath = os.path.join(DATA_DIR, "CloudFront/distributions.json")
            save_data_to_file(distributions, filepath)
        else:
            print("No CloudFront distributions found")
    except ClientError as e:
        print(f"Error fetching CloudFront data: {e}")

def collect_route53_data():
    route53 = boto3.client('route53')
    try:
        hosted_zones = route53.list_hosted_zones()
        if hosted_zones.get('HostedZones'):
            filepath = os.path.join(DATA_DIR, "Route53/hosted_zones.json")
            save_data_to_file(hosted_zones, filepath)
        else:
            print("No Route53 hosted zones found")
    except ClientError as e:
        print(f"Error fetching Route53 data: {e}")

def collect_secretsmanager_data():
    service_name = 'secretsmanager'
    regions = list_accessible_regions(service_name)
    for region in regions:
        try:
            sm = get_session(region).client(service_name)
            secrets = sm.list_secrets()
            if secrets.get('SecretList'):
                filepath = os.path.join(DATA_DIR, f"SecretsManager/{region}_secrets.json")
                save_data_to_file(secrets, filepath)
            else:
                print(f"No secrets found in region {region}")
        except ClientError as e:
            print(f"Error fetching Secrets Manager data in region {region}: {e}")

def collect_elasticbeanstalk_data():
    service_name = 'elasticbeanstalk'
    regions = list_accessible_regions(service_name)
    for region in regions:
        try:
            eb = get_session(region).client(service_name)
            applications = eb.describe_applications()
            if applications.get('Applications'):
                filepath = os.path.join(DATA_DIR, f"ElasticBeanstalk/{region}_applications.json")
                save_data_to_file(applications, filepath)
            else:
                print(f"No Elastic Beanstalk applications found in region {region}")
        except ClientError as e:
            print(f"Error fetching Elastic Beanstalk data in region {region}: {e}")

def collect_ecs_data():
    service_name = 'ecs'
    regions = list_accessible_regions(service_name)
    for region in regions:
        try:
            ecs = get_session(region).client(service_name)
            clusters = ecs.list_clusters()
            if clusters.get('clusterArns'):
                filepath = os.path.join(DATA_DIR, f"ECS/{region}_clusters.json")
                save_data_to_file(clusters, filepath)
            else:
                print(f"No ECS clusters found in region {region}")
        except ClientError as e:
            print(f"Error fetching ECS data in region {region}: {e}")

def collect_ecr_data():
    service_name = 'ecr'
    regions = list_accessible_regions(service_name)
    for region in regions:
        try:
            ecr = get_session(region).client(service_name)
            repositories = ecr.describe_repositories()
            if repositories.get('repositories'):
                filepath = os.path.join(DATA_DIR, f"ECR/{region}_repositories.json")
                save_data_to_file(repositories, filepath)
            else:
                print(f"No ECR repositories found in region {region}")
        except ClientError as e:
            print(f"Error fetching ECR data in region {region}: {e}")

def collect_all_data():
    print("Starting parallel data collection...")
    data_collection_functions = [
        collect_ec2_data,
        collect_s3_data,
        collect_vpc_data,
        collect_iam_data,
        collect_rds_data,
        collect_lambda_data,
        collect_cloudformation_data,
        collect_elb_data,
        collect_cloudwatch_data,
        collect_eks_data,
        collect_cloudtrail_data,
        collect_dynamodb_data,
        collect_sqs_data,
        collect_sns_data,
        collect_efs_data,
        collect_cloudfront_data,
        collect_route53_data,
        collect_secretsmanager_data,
        collect_elasticbeanstalk_data,
        collect_ecs_data,
        collect_ecr_data
    ]
    with concurrent.futures.ThreadPoolExecutor() as executor:
        future_to_func = {executor.submit(func): func.__name__ for func in data_collection_functions}
        for future in concurrent.futures.as_completed(future_to_func):
            func_name = future_to_func[future]
            try:
                future.result()
                print(f"{func_name} completed.")
            except Exception as exc:
                print(f"{func_name} generated an exception: {exc}")
    print("All data collection completed.")

def generate_data_tree():
    if not os.path.exists(DATA_DIR):
        print("No data directory found. Skipping data tree generation.")
        return ""
    tree_lines = []
    for root, dirs, files in os.walk(DATA_DIR):
        level = root.replace(DATA_DIR, '').count(os.sep)
        indent = ' ' * 4 * level
        subdir = os.path.basename(root)
        tree_lines.append(f"{indent}{subdir}/")
        subindent = ' ' * 4 * (level + 1)
        for f in files:
            tree_lines.append(f"{subindent}{f}")
    tree_string = '\n'.join(tree_lines)
    return tree_string


def setup_AWS_state(state):
    collect_all_data()
    tree_string = generate_data_tree()
    state["aws_data_tree"] = tree_string
    return state

from langchain_openai import ChatOpenAI
import google.generativeai as genai
import os

# Initialize the LLM (you can configure this globally if needed)
openai_llm = ChatOpenAI(model="gpt-4o", temperature=0, openai_api_key=os.getenv("OPENAI_API_KEY"))
genai_api_key = os.getenv("GOOGLE_API_KEY")
genai.configure(api_key=genai_api_key)
gemini_llm = genai.GenerativeModel(model_name="gemini-1.5-pro")

def generate_file_descriptions(repo_path: str) -> dict:
    """
    Generate natural language descriptions for IaC-related files in the repository.

    Args:
        repo_path (str): Path to the repository folder.

    Returns:
        dict: A dictionary with file paths as keys and their descriptions as values.
    """
    # Define IaC-related extensions
    iac_extensions = (
        '.tf', '.tfvars',  # Terraform
        '.yaml', '.yml',  # Kubernetes/Ansible/CloudFormation
        '.json',  # JSON for CloudFormation or Kubernetes
        '.sh',  # Shell scripts (for setup or IaC)
        '.cfg', '.ini',  # Configuration files
        '.ps1',  # PowerShell scripts
    )

    file_descriptions = {}

    for root, dirs, files in os.walk(repo_path):
        # Skip .git directory
        dirs[:] = [d for d in dirs if d != '.git']

        for file_name in files:
            file_path = os.path.join(root, file_name)

            # Skip non-IaC files based on extensions
            if not file_name.endswith(iac_extensions):
                continue

            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    file_content = f.read()
                    prompt = f"Provide a natural language description of the following IaC file:\n\n{file_content}"
                    description = openai_llm.invoke({"messages": [{"role": "user", "content": prompt}]})["content"]
                    file_descriptions[file_path] = description.strip()
            except Exception as e:
                print(f"Skipping file {file_path} due to error: {e}")

    return file_descriptions

def generate_codebase_overview(combined_file_path: str) -> str:
    """
    Generate a natural language overview of the codebase using Gemini.

    Args:
        file_descriptions (dict): A dictionary of file descriptions.

    Returns:
        str: A high-level natural language overview of the codebase.
    """
    with open(combined_file_path, "r", encoding="utf-8") as f:
        files = f.read()

    prompt = f"""
    Provide a natural language overview of the following codebase based on its file descriptions include 
    a breakdown of the file types and their purpose, the file tree, and a conerete overview of the codebase itself and its purpose. 
    
    {files}
    """
    
    overview = gemini_llm.generate_content(prompt).text

    return overview.strip()



