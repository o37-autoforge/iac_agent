---
parent: Connecting to LLMs
nav_order: 560
---

# Amazon Bedrock

forge can connect to models provided by Amazon Bedrock.
You will need to have an AWS account with access to the Bedrock service.

To configure forge to use the Amazon Bedrock API, you need to set up your AWS credentials.
This can be done using the AWS CLI or by setting environment variables.

## AWS CLI Configuration

If you haven't already, install the [AWS CLI](https://aws.amazon.com/cli/) and configure it with your credentials:

```bash
aws configure
```

This will prompt you to enter your AWS Access Key ID, Secret Access Key, and default region.

## Environment Variables

Alternatively, you can set the following environment variables:

```bash
export AWS_REGION=your_preferred_region

# For user authentication
export AWS_ACCESS_KEY_ID=your_access_key
export AWS_SECRET_ACCESS_KEY=your_secret_key

# For profile authentication
export AWS_PROFILE=your-profile
```

You can add these to your 
[.env file](/docs/config/dotenv.html).

## Bedrock with `pipx` installation

The AWS Bedrock provider requires the `boto3` package in order to function correctly. To use forge installed via `pipx` with AWS Bedrock, you must add the `boto3` dependency to forge's virtual environment by running

```
pipx inject forge boto3
```


## Running forge with Bedrock

Once your AWS credentials are set up, you can run forge with the `--model` command line switch, specifying the Bedrock model you want to use:

```bash
forge --model bedrock/anthropic.claude-3-5-sonnet-20240620-v1:0
```


## Available Models

To see some models available via Bedrock, run:

```
forge --list-models bedrock/
```

Make sure you have access to these models in your AWS account before attempting to use them with forge.

# More info

For more information on Amazon Bedrock and its models, refer to the [official AWS documentation](https://docs.aws.amazon.com/bedrock/latest/userguide/what-is-bedrock.html).

Also, see the 
[litellm docs on Bedrock](https://litellm.vercel.app/docs/providers/bedrock).
