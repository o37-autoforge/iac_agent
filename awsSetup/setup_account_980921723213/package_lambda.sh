#!/bin/bash

# Exit immediately if a command exits with a non-zero status.
set -e

# Define variables
LAMBDA_FUNCTION=lambda_function.py
ZIP_FILE=lambda_function.zip
PACKAGE_DIR=lambda_package

# Create a clean package directory
rm -rf $PACKAGE_DIR
mkdir $PACKAGE_DIR

# Install dependencies
pip install psycopg2-binary -t $PACKAGE_DIR

# Copy the Lambda function code
cp $LAMBDA_FUNCTION $PACKAGE_DIR/

# Navigate to the package directory
cd $PACKAGE_DIR

# Zip the contents
zip -r ../$ZIP_FILE .

# Navigate back
cd ..

# Clean up
rm -rf $PACKAGE_DIR

echo "Lambda function packaged successfully into $ZIP_FILE."
