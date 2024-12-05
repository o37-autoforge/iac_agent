CREATE TABLE aws_resources (
    resource_id VARCHAR PRIMARY KEY,
    resource_type VARCHAR,
    account_id VARCHAR,
    region VARCHAR,
    configuration JSONB,
    last_updated TIMESTAMP
);

-- Create Indexes for Faster Queries
CREATE INDEX idx_resource_type ON aws_resources(resource_type);
CREATE INDEX idx_account_id ON aws_resources(account_id);
CREATE INDEX idx_region ON aws_resources(region);
CREATE INDEX idx_last_updated ON aws_resources(last_updated);
