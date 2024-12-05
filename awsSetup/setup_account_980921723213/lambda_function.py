import json
import boto3
import psycopg2
import os
from urllib.parse import unquote_plus

def lambda_handler(event, context):
    # Initialize clients
    s3 = boto3.client('s3')
    secrets_client = boto3.client('secretsmanager')

    # Retrieve environment variables
    secret_name = os.environ['SECRET_NAME']
    db_name = os.environ['DB_NAME']
    db_host = os.environ['DB_HOST']

    # Get secret from Secrets Manager
    try:
        secret_response = secrets_client.get_secret_value(SecretId=secret_name)
        secret = json.loads(secret_response['SecretString'])
        db_username = secret['username']
        db_password = secret['password']
    except Exception as e:
        print(f"Error retrieving secret: {e}")
        return {
            'statusCode': 500,
            'body': json.dumps('Failed to retrieve database credentials.')
        }

    # Connect to PostgreSQL
    try:
        conn = psycopg2.connect(
            host=db_host,
            database=db_name,
            user=db_username,
            password=db_password
        )
    except Exception as e:
        print(f"Database connection error: {e}")
        return {
            'statusCode': 500,
            'body': json.dumps('Database connection failed.')
        }

    cursor = conn.cursor()

    # Process each record in the event
    for record in event.get('Records', []):
        # Parse S3 event
        if record['eventName'] not in ['ObjectCreated:Put', 'ObjectCreated:Post']:
            continue  # Ignore other events

        bucket = record['s3']['bucket']['name']
        key = unquote_plus(record['s3']['object']['key'])

        # Get the S3 object
        try:
            response = s3.get_object(Bucket=bucket, Key=key)
            config_data = json.loads(response['Body'].read().decode('utf-8'))
        except Exception as e:
            print(f"Error reading S3 object {key} from bucket {bucket}: {e}")
            continue

        # Process each configuration item
        for item in config_data.get('configurationItems', []):
            resource_id = item.get('resourceId')
            resource_type = item.get('resourceType')
            account_id = item.get('accountId')
            region = item.get('awsRegion')
            configuration = json.dumps(item.get('configuration', {}))
            last_updated = item.get('configurationItemCaptureTime')

            # Upsert into the database
            try:
                cursor.execute("""
                    INSERT INTO aws_resources (resource_id, resource_type, account_id, region, configuration, last_updated)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (resource_id)
                    DO UPDATE SET
                        resource_type = EXCLUDED.resource_type,
                        account_id = EXCLUDED.account_id,
                        region = EXCLUDED.region,
                        configuration = EXCLUDED.configuration,
                        last_updated = EXCLUDED.last_updated;
                """, (resource_id, resource_type, account_id, region, configuration, last_updated))
            except Exception as e:
                print(f"Error upserting resource {resource_id}: {e}")

    # Commit and close the database connection
    try:
        conn.commit()
    except Exception as e:
        print(f"Error committing transaction: {e}")
    finally:
        cursor.close()
        conn.close()

    return {
        'statusCode': 200,
        'body': json.dumps('AWS Config data processed successfully.')
    }
