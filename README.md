# AWS Lambda CloudWatch Automation

Serverless automation toolkit using AWS Lambda and CloudWatch to monitor infrastructure health, auto-remediate common issues, and send intelligent alerts. Built for production support engineers who want to reduce manual intervention.

## Features

- **EC2 Health Monitor** — Automatically restarts unhealthy instances and notifies the team
- **RDS Storage Alert** — Monitors database storage and triggers auto-scaling before critical thresholds
- **Cost Anomaly Detector** — Detects unusual spending spikes and sends daily cost reports
- **Log Error Aggregator** — Scans CloudWatch logs for error patterns and creates summary reports
- **Stale Resource Cleaner** — Identifies and reports unused EBS volumes, old snapshots, and unattached IPs

## Architecture

```
CloudWatch Events (Scheduled)
        │
        ▼
  ┌─────────────┐     ┌──────────────┐     ┌─────────────┐
  │   Lambda     │────▶│  CloudWatch   │────▶│    SNS      │
  │  Functions   │     │   Metrics     │     │  (Alerts)   │
  └──────┬──────┘     └──────────────┘     └──────┬──────┘
         │                                         │
         ▼                                         ▼
  ┌─────────────┐                          ┌─────────────┐
  │  S3 Bucket  │                          │   Email /   │
  │  (Reports)  │                          │   Slack     │
  └─────────────┘                          └─────────────┘
```

## Project Structure

```
.
├── functions/
│   ├── ec2_health_monitor/
│   │   └── lambda_function.py
│   ├── rds_storage_alert/
│   │   └── lambda_function.py
│   ├── cost_anomaly_detector/
│   │   └── lambda_function.py
│   ├── log_error_aggregator/
│   │   └── lambda_function.py
│   └── stale_resource_cleaner/
│       └── lambda_function.py
├── deploy.sh
├── requirements.txt
└── README.md
```

## Quick Start

```bash
# Clone repository
git clone https://github.com/chinzorig11/lambda-cloudwatch-automation.git
cd lambda-cloudwatch-automation

# Configure AWS CLI
aws configure

# Deploy all functions
chmod +x deploy.sh
./deploy.sh

# Or deploy individually
cd functions/ec2_health_monitor
zip function.zip lambda_function.py
aws lambda update-function-code \
  --function-name ec2-health-monitor \
  --zip-file fileb://function.zip
```

## Configuration

Set environment variables for each Lambda function:

| Variable | Description | Default |
|----------|-------------|---------|
| `SNS_TOPIC_ARN` | SNS topic for notifications | Required |
| `S3_BUCKET` | S3 bucket for reports | Required |
| `ENVIRONMENT` | Environment tag filter | `production` |
| `CPU_THRESHOLD` | CPU alert threshold (%) | `80` |
| `STORAGE_THRESHOLD_GB` | RDS storage alert (GB) | `5` |
| `COST_SPIKE_PERCENT` | Cost anomaly threshold (%) | `20` |

## Author

**Chinzorig Ochirbat** — [GitHub](https://github.com/chinzorig11) | [LinkedIn](https://linkedin.com/in/chinzorig-o-53578021b)
