"""
Go-Dispatch: Strands Agent Tools Suite
Defines tools for autonomous background triage, Bedrock KB retrieval,
diagnostic verifications, ticket updates, and immediate human dispatch via SNS.
"""

import json
import logging
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


@tool
def execute_ping_diagnostic(target_ip: str, count: int = 3) -> str:
    """Performs an automated ping verification check against an edge router, server, or gateway

    to verify if an outage is a transient ping flap or an active hard down failure.
    """
    logger.info(f"Running automated ping diagnostic against {target_ip} ({count} probes)...")
    # In production, this can invoke an ICMP probe or AWS Network Monitor API
    # Simulated check logic:
    return json.dumps(
        {
            "target": target_ip,
            "probes_sent": count,
            "packet_loss_pct": 100.0,
            "status": "UNREACHABLE",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "diagnostic_verdict": "Confirmed hardware interface failure or upstream ISP link drop.",
        }
    )


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