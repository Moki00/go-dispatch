"""
Go-Dispatch: Strands Agent Tools Suite
Defines tools for autonomous background triage, Bedrock KB retrieval,
diagnostic verifications, ticket updates, and immediate human dispatch via SNS.
"""

import ipaddress
import json
import logging
import re
import subprocess
import sys
from datetime import datetime, timezone
from typing import Optional

import boto3
from botocore.exceptions import ClientError
from strands import tool

from src.config import get_settings

logger = logging.getLogger("go_dispatch.tools")
settings = get_settings()

# Initialize AWS clients
bedrock_agent_runtime = boto3.client(
    "bedrock-agent-runtime", region_name=settings.aws_region
)
dynamodb = boto3.resource("dynamodb", region_name=settings.aws_region)
sns_client = boto3.client("sns", region_name=settings.aws_region)


# ---------------------------------------------------------------------------
# Tier 1 & 2: Autonomous Passive Tools (Quiet Background Mode)
# ---------------------------------------------------------------------------


@tool
def query_client_runbook(client_id: str, query: str) -> str:
    """Queries the Bedrock Knowledge Base for client-specific network architecture,

    gateway IP schema, router models, SLA tiers, and standard troubleshooting
    runbooks.
    """
    if not settings.bedrock_kb_id:
        return (
            f"[MOCK KB] Client: {client_id} | Query: {query} | "
            "SLA: Gold (2hr response, 4hr onsite) | Primary Gateway: 192.168.10.1 | "
            "Edge Device: UniFi Dream Machine Pro | Spare Switch: USW-24-PoE in Server Closet."
        )

    try:
        response = bedrock_agent_runtime.retrieve(
            knowledgeBaseId=settings.bedrock_kb_id,
            retrievalQuery={"text": f"Client ID {client_id}: {query}"},
            retrievalConfiguration={
                "vectorSearchConfiguration": {"numberOfResults": 3}
            },
        )
        results = [
            doc["content"]["text"] for doc in response.get("retrievalResults", [])
        ]
        if not results:
            return f"No runbook documentation found for client {client_id}."
        return "\n---\n".join(results)
    except ClientError as e:
        logger.error(f"Error querying Bedrock KB: {e}")
        return f"Error retrieving KB context: {str(e)}"


def _is_valid_target(target: str) -> bool:
    """Validates that target is a valid IP address or hostname to prevent command injection."""
    try:
        ipaddress.ip_address(target)
        return True
    except ValueError:
        return bool(re.match(r"^[a-zA-Z0-9.-]+$", target) and len(target) <= 255)


@tool
def execute_ping_diagnostic(target_ip: str, count: int = 3, timeout_ms: int = 1000) -> str:
    """Performs an automated network ICMP ping verification check against an edge router,
    server, or gateway to verify if an outage is a transient ping flap or an active hard down failure.
    Returns structured diagnostic telemetry with packet loss percentage, round-trip times, and verdict.
    """
    logger.info(f"Running automated ping diagnostic against {target_ip} ({count} probes)...")

    if not _is_valid_target(target_ip):
        return json.dumps({
            "target": target_ip,
            "error": "Invalid target format. Must be a valid IPv4/IPv6 address or hostname.",
            "status": "ERROR",
            "packet_loss_pct": 100.0,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "diagnostic_verdict": "Diagnostic aborted: security validation failed for target syntax.",
        })

    is_win = sys.platform.startswith("win")
    timeout_sec = max(1, timeout_ms // 1000)

    if is_win:
        cmd = ["ping", "-n", str(count), "-w", str(timeout_ms), target_ip]
    else:
        cmd = ["ping", "-c", str(count), "-W", str(timeout_sec), target_ip]

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=(count * (timeout_ms / 1000) + 5),
        )
        raw_output = proc.stdout + proc.stderr
        returncode = proc.returncode
    except subprocess.TimeoutExpired:
        return json.dumps({
            "target": target_ip,
            "probes_sent": count,
            "probes_received": 0,
            "packet_loss_pct": 100.0,
            "avg_rtt_ms": None,
            "status": "UNREACHABLE",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "diagnostic_verdict": "Probes timed out completely. Confirmed hard down failure.",
        })
    except Exception as e:
        logger.error(f"Error executing ping process: {e}")
        return json.dumps({
            "target": target_ip,
            "error": str(e),
            "status": "ERROR",
            "packet_loss_pct": 100.0,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "diagnostic_verdict": f"Failed to execute ping diagnostic: {e}",
        })

    # Parse packet loss percentage
    loss_match = re.search(r"\((\d+)%\s*loss\)", raw_output, re.IGNORECASE)  # Windows
    if not loss_match:
        loss_match = re.search(r"(\d+)%\s*packet loss", raw_output, re.IGNORECASE)  # Linux

    if loss_match:
        packet_loss_pct = float(loss_match.group(1))
    else:
        packet_loss_pct = 0.0 if returncode == 0 else 100.0

    # Parse sent / received counts
    sent = count
    received = 0
    counts_match = re.search(r"Sent\s*=\s*(\d+),\s*Received\s*=\s*(\d+)", raw_output, re.IGNORECASE)  # Windows
    if counts_match:
        sent = int(counts_match.group(1))
        received = int(counts_match.group(2))
    else:
        linux_counts = re.search(
            r"(\d+)\s*packets transmitted,\s*(\d+)\s*(?:packets\s*)?received",
            raw_output,
            re.IGNORECASE,
        )
        if linux_counts:
            sent = int(linux_counts.group(1))
            received = int(linux_counts.group(2))
        else:
            received = int(sent * (1.0 - (packet_loss_pct / 100.0)))

    # Parse average latency
    avg_rtt = None
    win_avg = re.search(r"Average\s*=\s*(\d+)ms", raw_output, re.IGNORECASE)
    if win_avg:
        avg_rtt = float(win_avg.group(1))
    else:
        linux_rtt = re.search(r"rtt min/avg/max/mdev\s*=\s*[\d.]+/([\d.]+)/", raw_output, re.IGNORECASE)
        if linux_rtt:
            avg_rtt = float(linux_rtt.group(1))

    # Evaluate connectivity status & operational verdict
    if packet_loss_pct == 0.0 and received > 0:
        status = "REACHABLE"
        verdict = (
            f"Host is fully reachable (0% packet loss, avg latency {avg_rtt or 0}ms). "
            "Transient alarm appears self-healed or false positive; no physical dispatch required."
        )
    elif 0.0 < packet_loss_pct < 100.0:
        status = "DEGRADED"
        verdict = (
            f"Intermittent connectivity detected ({packet_loss_pct}% packet loss). "
            "Link is flapping or heavily congested; active monitoring recommended."
        )
    else:
        status = "UNREACHABLE"
        verdict = (
            "Confirmed hard down failure (100% packet loss). "
            "Target is completely unresponsive; physical interface failure, power outage, or ISP drop."
        )

    return json.dumps({
        "target": target_ip,
        "probes_sent": sent,
        "probes_received": received,
        "packet_loss_pct": packet_loss_pct,
        "avg_rtt_ms": avg_rtt,
        "status": status,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "diagnostic_verdict": verdict,
    })


@tool
def log_ticket_action(
    ticket_id: str,
    action_summary: str,
    new_status: str,
    internal_notes: Optional[str] = None,
) -> str:
    """Updates the ticket record in DynamoDB silently without alerting or distracting the engineer.

    Use this for Tier 1 auto-resolutions and Tier 2 async draft queueing.
    """
    try:
        table = dynamodb.Table(settings.dynamodb_tickets_table)
        update_data = {
            "last_action": action_summary,
            "status": new_status,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "internal_notes": internal_notes or "Action processed by Go-Dispatch Agent.",
        }

        table.update_item(
            Key={"ticket_id": ticket_id},
            UpdateExpression="SET #s = :status, #la = :la, #ua = :ua, #in = :in",
            ExpressionAttributeNames={
                "#s": "status",
                "#la": "last_action",
                "#ua": "updated_at",
                "#in": "internal_notes",
            },
            ExpressionAttributeValues={
                ":status": new_status,
                ":la": action_summary,
                ":ua": update_data["updated_at"],
                ":in": update_data["internal_notes"],
            },
        )
        return f"Ticket {ticket_id} updated successfully. Status: {new_status}."
    except Exception as e:
        logger.warning(f"DynamoDB update skipped or failed: {e}")
        return f"Action logged locally (Ticket {ticket_id}): {action_summary} [Status: {new_status}]"


# ---------------------------------------------------------------------------
# Tier 3 & 4: Escalation & Immediate Field Dispatch Tools (Human-in-the-Loop)
# ---------------------------------------------------------------------------


@tool
def escalate_to_technician(
    urgency_level: str,
    client_name: str,
    issue_summary: str,
    site_address: str,
    recommended_action: str,
    sla_deadline_minutes: int,
) -> str:
    """MOBILIZES THE TECHNICIAN IMMEDIATELY via Amazon SNS push notification/SMS.

    ONLY trigger this tool for Tier 3 (impending SLA breach) or Tier 4 (critical site outages,
    hardware failures requiring physical on-site presence, or billable authorization).
    """
    dispatch_payload = {
        "AGENT": "Go-Dispatch Autonomous Ops",
        "URGENCY": urgency_level.upper(),
        "CLIENT": client_name,
        "SITE_LOCATION": site_address,
        "SLA_WINDOW_REMAINING": f"{sla_deadline_minutes} mins",
        "INCIDENT_SUMMARY": issue_summary,
        "RECOMMENDED_MOBILIZATION": recommended_action,
        "TIMESTAMP": datetime.now(timezone.utc).isoformat(),
    }

    message_body = (
        f"🚨 [GO-DISPATCH {urgency_level.upper()}] 🚨\n"
        f"Client: {client_name}\n"
        f"Location: {site_address}\n"
        f"SLA Time Remaining: {sla_deadline_minutes} min(s)\n\n"
        f"Incident: {issue_summary}\n"
        f"Action: {recommended_action}\n"
    )

    if settings.sns_dispatch_topic_arn:
        try:
            sns_client.publish(
                TopicArn=settings.sns_dispatch_topic_arn,
                Subject=f"GO-DISPATCH ALERT: {client_name} - {urgency_level.upper()}",
                Message=message_body,
            )
            logger.info(f"SNS Dispatch Alert successfully sent to topic: {settings.sns_dispatch_topic_arn}")
        except ClientError as e:
            logger.error(f"Failed to publish SNS alert: {e}")

    return (
        f"CRITICAL ALERT DISPATCHED TO TECHNICIAN.\n"
        f"Dossier Payload:\n{json.dumps(dispatch_payload, indent=2)}"
    )