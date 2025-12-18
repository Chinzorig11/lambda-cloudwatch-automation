"""
Stale Resource Cleaner Lambda Function
Identifies unused EBS volumes, old snapshots, and unattached
Elastic IPs. Generates cleanup report without deleting.

Trigger: CloudWatch EventBridge rule (weekly, Sunday 08:00 UTC)
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
sns_client = boto3.client("sns")
s3_client = boto3.client("s3")

SNS_TOPIC_ARN = os.environ.get("SNS_TOPIC_ARN")
S3_BUCKET = os.environ.get("S3_BUCKET")
SNAPSHOT_AGE_DAYS = int(os.environ.get("SNAPSHOT_AGE_DAYS", "90"))


def lambda_handler(event, context):
    """Main handler: scan for stale resources."""
    logger.info("Starting stale resource scan")

    unattached_volumes = find_unattached_volumes()
    old_snapshots = find_old_snapshots()
    unattached_eips = find_unattached_eips()

    total_monthly_waste = estimate_monthly_waste(
        unattached_volumes, old_snapshots, unattached_eips
    )

    report = build_report(
        unattached_volumes, old_snapshots, unattached_eips, total_monthly_waste
    )

    save_report_to_s3(report)

    if unattached_volumes or old_snapshots or unattached_eips:
        send_alert(report, total_monthly_waste)

    return {
        "statusCode": 200,
        "body": json.dumps({
            "unattached_volumes": len(unattached_volumes),
            "old_snapshots": len(old_snapshots),
            "unattached_eips": len(unattached_eips),
            "estimated_monthly_waste": round(total_monthly_waste, 2),
        }),
    }


def find_unattached_volumes():
    """Find EBS volumes not attached to any instance."""
    try:
        response = ec2_client.describe_volumes(
            Filters=[{"Name": "status", "Values": ["available"]}]
        )
        volumes = []
        for vol in response["Volumes"]:
            name = ""
            for tag in vol.get("Tags", []):
                if tag["Key"] == "Name":
                    name = tag["Value"]
            volumes.append({
                "id": vol["VolumeId"],
                "name": name,
                "size_gb": vol["Size"],
                "type": vol["VolumeType"],
                "created": vol["CreateTime"].isoformat(),
            })
        logger.info(f"Found {len(volumes)} unattached volumes")
        return volumes
    except ClientError as e:
        logger.error(f"Error finding volumes: {e}")
        return []


def find_old_snapshots():
    """Find snapshots older than threshold."""
    cutoff = datetime.utcnow() - timedelta(days=SNAPSHOT_AGE_DAYS)
    try:
        response = ec2_client.describe_snapshots(OwnerIds=["self"])
        old = []
        for snap in response["Snapshots"]:
            if snap["StartTime"].replace(tzinfo=None) < cutoff:
                old.append({
                    "id": snap["SnapshotId"],
                    "size_gb": snap["VolumeSize"],
                    "created": snap["StartTime"].isoformat(),
                    "description": snap.get("Description", "")[:50],
                })
        logger.info(f"Found {len(old)} snapshots older than {SNAPSHOT_AGE_DAYS} days")
        return old
    except ClientError as e:
        logger.error(f"Error finding snapshots: {e}")
        return []


def find_unattached_eips():
    """Find Elastic IPs not associated with any instance."""
    try:
        response = ec2_client.describe_addresses()
        unattached = []
        for addr in response["Addresses"]:
            if "InstanceId" not in addr and "NetworkInterfaceId" not in addr:
                unattached.append({
                    "ip": addr["PublicIp"],
                    "allocation_id": addr["AllocationId"],
                })
        logger.info(f"Found {len(unattached)} unattached Elastic IPs")
        return unattached
    except ClientError as e:
        logger.error(f"Error finding EIPs: {e}")
        return []


def estimate_monthly_waste(volumes, snapshots, eips):
    """Estimate monthly cost of stale resources."""
    vol_cost = sum(v["size_gb"] * 0.10 for v in volumes)  # ~$0.10/GB/mo gp3
    snap_cost = sum(s["size_gb"] * 0.05 for s in snapshots)  # ~$0.05/GB/mo
    eip_cost = len(eips) * 3.60  # ~$3.60/mo per unused EIP
    return vol_cost + snap_cost + eip_cost


def build_report(volumes, snapshots, eips, total_waste):
    """Build formatted cleanup report."""
    lines = [
        "=" * 55,
        "  AWS Stale Resource Report",
        f"  Generated: {datetime.utcnow().isoformat()}Z",
        "=" * 55,
        f"",
        f"  Estimated Monthly Waste: ${total_waste:.2f}",
        f"",
    ]

    lines.append(f"  Unattached EBS Volumes ({len(volumes)}):")
    lines.append("-" * 55)
    for v in volumes[:20]:
        lines.append(f"  {v['id']}  {v['size_gb']:>5}GB  {v['type']}")

    lines.append(f"")
    lines.append(f"  Old Snapshots ({len(snapshots)}, >{SNAPSHOT_AGE_DAYS} days):")
    lines.append("-" * 55)
    for s in snapshots[:20]:
        lines.append(f"  {s['id']}  {s['size_gb']:>5}GB  {s['description']}")

    lines.append(f"")
    lines.append(f"  Unattached Elastic IPs ({len(eips)}):")
    lines.append("-" * 55)
    for e in eips:
        lines.append(f"  {e['ip']}  ({e['allocation_id']})")

    lines.append("=" * 55)
    lines.append("  NOTE: No resources were deleted. Review and clean manually.")
    lines.append("=" * 55)
    return "\n".join(lines)


def save_report_to_s3(report):
    if not S3_BUCKET:
        return
    today = datetime.utcnow().date()
    key = f"stale-reports/{today.year}/{today.month:02d}/stale-{today}.txt"
    try:
        s3_client.put_object(Bucket=S3_BUCKET, Key=key, Body=report)
        logger.info(f"Report saved to s3://{S3_BUCKET}/{key}")
    except ClientError as e:
        logger.error(f"Failed to save report: {e}")


def send_alert(report, total_waste):
    if not SNS_TOPIC_ARN:
        return
    try:
        sns_client.publish(
            TopicArn=SNS_TOPIC_ARN,
            Subject=f"[INFO] Stale Resources Found — ${total_waste:.2f}/mo waste",
            Message=report,
        )
    except ClientError as e:
        logger.error(f"Failed to send alert: {e}")
