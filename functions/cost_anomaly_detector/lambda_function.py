"""
Cost Anomaly Detector Lambda Function
Compares today's AWS spending against 7-day average and alerts
on unusual spikes. Sends daily cost summary reports.

Trigger: CloudWatch EventBridge rule (daily at 09:00 UTC)
"""

import json
import os
import logging
from datetime import datetime, timedelta

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

ce_client = boto3.client("ce")
sns_client = boto3.client("sns")
s3_client = boto3.client("s3")

SNS_TOPIC_ARN = os.environ.get("SNS_TOPIC_ARN")
S3_BUCKET = os.environ.get("S3_BUCKET")
COST_SPIKE_PERCENT = float(os.environ.get("COST_SPIKE_PERCENT", "20"))


def lambda_handler(event, context):
    """Main handler: analyze costs and detect anomalies."""
    logger.info("Starting cost anomaly detection")

    today = datetime.utcnow().date()
    yesterday = today - timedelta(days=1)

    # Get yesterday's cost
    yesterday_cost = get_daily_cost(yesterday)

    # Get 7-day average (excluding yesterday)
    seven_day_avg = get_average_cost(
        start=today - timedelta(days=8),
        end=today - timedelta(days=1),
    )

    # Get cost breakdown by service
    service_costs = get_cost_by_service(yesterday)

    # Detect anomaly
    is_anomaly = False
    spike_percent = 0
    if seven_day_avg > 0:
        spike_percent = ((yesterday_cost - seven_day_avg) / seven_day_avg) * 100
        is_anomaly = spike_percent > COST_SPIKE_PERCENT

    # Build report
    report = build_report(
        date=yesterday,
        daily_cost=yesterday_cost,
        average_cost=seven_day_avg,
        spike_percent=spike_percent,
        is_anomaly=is_anomaly,
        service_costs=service_costs,
    )

    # Save report to S3
    save_report_to_s3(report, yesterday)

    # Send alert if anomaly detected
    if is_anomaly:
        send_anomaly_alert(report)
    else:
        send_daily_summary(report)

    return {
        "statusCode": 200,
        "body": json.dumps({
            "date": str(yesterday),
            "daily_cost": round(yesterday_cost, 2),
            "seven_day_avg": round(seven_day_avg, 2),
            "spike_percent": round(spike_percent, 1),
            "is_anomaly": is_anomaly,
        }),
    }


def get_daily_cost(date):
    """Get total AWS cost for a specific date."""
    try:
        response = ce_client.get_cost_and_usage(
            TimePeriod={
                "Start": str(date),
                "End": str(date + timedelta(days=1)),
            },
            Granularity="DAILY",
            Metrics=["UnblendedCost"],
        )
        results = response["ResultsByTime"]
        if results:
            return float(results[0]["Total"]["UnblendedCost"]["Amount"])
        return 0.0
    except ClientError as e:
        logger.error(f"Error fetching daily cost: {e}")
        return 0.0


def get_average_cost(start, end):
    """Get average daily cost over a date range."""
    try:
        response = ce_client.get_cost_and_usage(
            TimePeriod={"Start": str(start), "End": str(end)},
            Granularity="DAILY",
            Metrics=["UnblendedCost"],
        )
        costs = [
            float(r["Total"]["UnblendedCost"]["Amount"])
            for r in response["ResultsByTime"]
        ]
        return sum(costs) / len(costs) if costs else 0.0
    except ClientError as e:
        logger.error(f"Error fetching average cost: {e}")
        return 0.0


def get_cost_by_service(date):
    """Get cost breakdown by AWS service."""
    try:
        response = ce_client.get_cost_and_usage(
            TimePeriod={
                "Start": str(date),
                "End": str(date + timedelta(days=1)),
            },
            Granularity="DAILY",
            Metrics=["UnblendedCost"],
            GroupBy=[{"Type": "DIMENSION", "Key": "SERVICE"}],
        )
        services = {}
        for result in response["ResultsByTime"]:
            for group in result["Groups"]:
                service_name = group["Keys"][0]
                cost = float(group["Metrics"]["UnblendedCost"]["Amount"])
                if cost > 0.01:
                    services[service_name] = round(cost, 2)

        return dict(
            sorted(services.items(), key=lambda x: x[1], reverse=True)
        )
    except ClientError as e:
        logger.error(f"Error fetching service costs: {e}")
        return {}


def build_report(date, daily_cost, average_cost, spike_percent, is_anomaly, service_costs):
    """Build a formatted cost report."""
    status = "ANOMALY DETECTED" if is_anomaly else "Normal"

    lines = [
        "=" * 50,
        f"  AWS Daily Cost Report - {date}",
        "=" * 50,
        f"",
        f"  Status:          {status}",
        f"  Yesterday:       ${daily_cost:.2f}",
        f"  7-Day Average:   ${average_cost:.2f}",
        f"  Change:          {spike_percent:+.1f}%",
        f"",
        "-" * 50,
        f"  Cost Breakdown by Service",
        "-" * 50,
    ]

    for service, cost in list(service_costs.items())[:10]:
        short_name = service.replace("Amazon ", "").replace("AWS ", "")[:30]
        lines.append(f"  {short_name:<32} ${cost:>8.2f}")

    total = sum(service_costs.values())
    lines.extend([
        "-" * 50,
        f"  {'TOTAL':<32} ${total:>8.2f}",
        "=" * 50,
    ])

    return "\n".join(lines)


def save_report_to_s3(report, date):
    """Save cost report to S3 bucket."""
    if not S3_BUCKET:
        logger.warning("S3_BUCKET not set, skipping report save")
        return

    key = f"cost-reports/{date.year}/{date.month:02d}/cost-report-{date}.txt"
    try:
        s3_client.put_object(
            Bucket=S3_BUCKET,
            Key=key,
            Body=report,
            ContentType="text/plain",
        )
        logger.info(f"Report saved to s3://{S3_BUCKET}/{key}")
    except ClientError as e:
        logger.error(f"Failed to save report: {e}")


def send_anomaly_alert(report):
    """Send urgent anomaly alert via SNS."""
    if not SNS_TOPIC_ARN:
        return
    try:
        sns_client.publish(
            TopicArn=SNS_TOPIC_ARN,
            Subject="[ALERT] AWS Cost Anomaly Detected",
            Message=report,
        )
        logger.info("Anomaly alert sent")
    except ClientError as e:
        logger.error(f"Failed to send anomaly alert: {e}")


def send_daily_summary(report):
    """Send daily cost summary via SNS."""
    if not SNS_TOPIC_ARN:
        return
    try:
        sns_client.publish(
            TopicArn=SNS_TOPIC_ARN,
            Subject="AWS Daily Cost Summary",
            Message=report,
        )
        logger.info("Daily summary sent")
    except ClientError as e:
        logger.error(f"Failed to send summary: {e}")
