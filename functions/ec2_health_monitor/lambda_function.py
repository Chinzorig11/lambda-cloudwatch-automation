"""
EC2 Health Monitor Lambda Function
Checks EC2 instance health, restarts unhealthy instances,
and sends notifications via SNS.

Trigger: CloudWatch EventBridge rule (every 5 minutes)
"""

import json
import os
import logging
from datetime import datetime, timedelta

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

ec2_client = boto3.client("ec2")
cloudwatch_client = boto3.client("cloudwatch")
sns_client = boto3.client("sns")

SNS_TOPIC_ARN = os.environ.get("SNS_TOPIC_ARN")
ENVIRONMENT = os.environ.get("ENVIRONMENT", "production")
CPU_THRESHOLD = float(os.environ.get("CPU_THRESHOLD", "80"))


def lambda_handler(event, context):
    """Main handler: check all tagged EC2 instances."""
    logger.info("Starting EC2 health check")

    instances = get_tagged_instances()
    issues = []

    for instance in instances:
        instance_id = instance["InstanceId"]
        state = instance["State"]["Name"]

        # Check if instance is running
        if state != "running":
            logger.info(f"Instance {instance_id} is {state}, skipping")
            continue

        # Check system status
        status_ok = check_instance_status(instance_id)
        if not status_ok:
            logger.warning(f"Instance {instance_id} has failed status checks")
            issues.append({
                "instance_id": instance_id,
                "issue": "failed_status_check",
                "action": "reboot_attempted",
            })
            reboot_instance(instance_id)

        # Check CPU utilization
        cpu_usage = get_cpu_utilization(instance_id)
        if cpu_usage and cpu_usage > CPU_THRESHOLD:
            logger.warning(
                f"Instance {instance_id} CPU at {cpu_usage:.1f}% "
                f"(threshold: {CPU_THRESHOLD}%)"
            )
            issues.append({
                "instance_id": instance_id,
                "issue": "high_cpu",
                "cpu_percent": round(cpu_usage, 1),
            })

    # Send notification if issues found
    if issues:
        send_alert(issues)
        publish_custom_metric(len(issues))

    return {
        "statusCode": 200,
        "body": json.dumps({
            "checked": len(instances),
            "issues": len(issues),
            "timestamp": datetime.utcnow().isoformat(),
        }),
    }


def get_tagged_instances():
    """Get EC2 instances tagged with the target environment."""
    try:
        response = ec2_client.describe_instances(
            Filters=[
                {"Name": "tag:Environment", "Values": [ENVIRONMENT]},
                {"Name": "instance-state-name", "Values": ["running", "stopped"]},
            ]
        )
        instances = []
        for reservation in response["Reservations"]:
            instances.extend(reservation["Instances"])
        logger.info(f"Found {len(instances)} instances tagged '{ENVIRONMENT}'")
        return instances
    except ClientError as e:
        logger.error(f"Error fetching instances: {e}")
        return []


def check_instance_status(instance_id):
    """Check EC2 system and instance status checks."""
    try:
        response = ec2_client.describe_instance_status(
            InstanceIds=[instance_id]
        )
        if not response["InstanceStatuses"]:
            return True

        status = response["InstanceStatuses"][0]
        system_ok = status["SystemStatus"]["Status"] == "ok"
        instance_ok = status["InstanceStatus"]["Status"] == "ok"
        return system_ok and instance_ok
    except ClientError as e:
        logger.error(f"Error checking status for {instance_id}: {e}")
        return True


def get_cpu_utilization(instance_id):
    """Get average CPU utilization over the last 10 minutes."""
    try:
        response = cloudwatch_client.get_metric_statistics(
            Namespace="AWS/EC2",
            MetricName="CPUUtilization",
            Dimensions=[{"Name": "InstanceId", "Value": instance_id}],
            StartTime=datetime.utcnow() - timedelta(minutes=10),
            EndTime=datetime.utcnow(),
            Period=300,
            Statistics=["Average"],
        )
        datapoints = response.get("Datapoints", [])
        if datapoints:
            latest = sorted(datapoints, key=lambda x: x["Timestamp"])[-1]
            return latest["Average"]
        return None
    except ClientError as e:
        logger.error(f"Error fetching CPU for {instance_id}: {e}")
        return None


def reboot_instance(instance_id):
    """Reboot an unhealthy EC2 instance."""
    try:
        ec2_client.reboot_instances(InstanceIds=[instance_id])
        logger.info(f"Successfully rebooted instance {instance_id}")
    except ClientError as e:
        logger.error(f"Failed to reboot {instance_id}: {e}")


def send_alert(issues):
    """Send SNS notification with issue details."""
    if not SNS_TOPIC_ARN:
        logger.warning("SNS_TOPIC_ARN not set, skipping notification")
        return

    subject = f"[{ENVIRONMENT.upper()}] EC2 Health Alert - {len(issues)} issue(s)"
    message_lines = [
        f"EC2 Health Monitor Report",
        f"Environment: {ENVIRONMENT}",
        f"Time: {datetime.utcnow().isoformat()}Z",
        f"Issues Found: {len(issues)}",
        "",
    ]

    for issue in issues:
        message_lines.append(f"Instance: {issue['instance_id']}")
        message_lines.append(f"  Issue: {issue['issue']}")
        if "cpu_percent" in issue:
            message_lines.append(f"  CPU: {issue['cpu_percent']}%")
        if "action" in issue:
            message_lines.append(f"  Action: {issue['action']}")
        message_lines.append("")

    try:
        sns_client.publish(
            TopicArn=SNS_TOPIC_ARN,
            Subject=subject,
            Message="\n".join(message_lines),
        )
        logger.info("Alert sent successfully")
    except ClientError as e:
        logger.error(f"Failed to send alert: {e}")


def publish_custom_metric(issue_count):
    """Publish custom CloudWatch metric for tracking."""
    try:
        cloudwatch_client.put_metric_data(
            Namespace="Custom/EC2HealthMonitor",
            MetricData=[
                {
                    "MetricName": "IssueCount",
                    "Value": issue_count,
                    "Unit": "Count",
                    "Dimensions": [
                        {"Name": "Environment", "Value": ENVIRONMENT}
                    ],
                }
            ],
        )
    except ClientError as e:
        logger.error(f"Failed to publish metric: {e}")
