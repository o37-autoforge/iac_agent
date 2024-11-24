import boto3
from botocore.exceptions import ClientError
import json
import os

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
            # Check if there are any instances
            has_data = any(reservation.get('Instances') for reservation in instances.get('Reservations', []))
            if has_data:
                filepath = f"data/EC2/{region}_ec2_instances.json"
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
            filepath = "data/S3/s3_buckets.json"
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
                filepath = f"data/VPC/{region}_vpcs.json"
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
            filepath = "data/IAM/iam_data.json"
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
                filepath = f"data/RDS/{region}_rds_instances.json"
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
                filepath = f"data/Lambda/{region}_lambda_functions.json"
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
                filepath = f"data/CloudFormation/{region}_stacks.json"
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
                filepath = f"data/ELB/{region}_load_balancers.json"
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
                filepath = f"data/CloudWatch/{region}_alarms.json"
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
                filepath = f"data/EKS/{region}_clusters.json"
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
                filepath = f"data/CloudTrail/{region}_trails.json"
                save_data_to_file(trails, filepath)
            else:
                print(f"No CloudTrail trails found in region {region}")
        except ClientError as e:
            print(f"Error fetching CloudTrail data in region {region}: {e}")

def collect_all_data():
    print("Collecting EC2 data...")
    collect_ec2_data()
    print("Collecting S3 data...")
    collect_s3_data()
    print("Collecting VPC data...")
    collect_vpc_data()
    print("Collecting IAM data...")
    collect_iam_data()
    print("Collecting RDS data...")
    collect_rds_data()
    print("Collecting Lambda data...")
    collect_lambda_data()
    print("Collecting CloudFormation data...")
    collect_cloudformation_data()
    print("Collecting ELB data...")
    collect_elb_data()
    print("Collecting CloudWatch data...")
    collect_cloudwatch_data()
    print("Collecting EKS data...")
    collect_eks_data()
    print("Collecting CloudTrail data...")
    collect_cloudtrail_data()

def generate_data_tree():
    data_dir = 'data'
    tree_file = 'data_tree.txt'

    if not os.path.exists(data_dir):
        print("No data directory found. Skipping data tree generation.")
        return

    tree_lines = []

    for root, dirs, files in os.walk(data_dir):
        level = root.replace(data_dir, '').count(os.sep)
        indent = ' ' * 4 * level
        subdir = os.path.basename(root)
        tree_lines.append(f"{indent}{subdir}/")
        subindent = ' ' * 4 * (level + 1)
        for f in files:
            tree_lines.append(f"{subindent}{f}")

    try:
        with open(tree_file, 'w') as f:
            f.write('\n'.join(tree_lines))
        print(f"Data tree saved to {tree_file}")
    except Exception as e:
        print(f"Error saving data tree to file: {e}")

if __name__ == "__main__":
    collect_all_data()

