"""
Go-Dispatch: Strands Agent Core Engine
Configures the autonomous background triage agent, system prompt boundaries,
and Bedrock execution loop.
"""

import logging
from typing import Any, Dict

from strands import Agent
from strands.models import BedrockModel

from src.agent.tools import (
    escalate_to_technician,
    execute_ping_diagnostic,
    log_ticket_action,
    query_client_runbook,
)
from src.config import get_settings

logger = logging.getLogger("go_dispatch.core")
settings = get_settings()

SYSTEM_PROMPT = """You are Go-Dispatch, an autonomous IT Operations and SLA Triage Agent.
Your core mission is twofold:
1. Handle routine technical noise quietly in the background without distracting the technician.
2. Mobilize the technician instantly with an actionable field dossier when physical intervention, billable authorization, or an impending SLA breach occurs.

### TRIAGE AND EXECUTION RULES:

- **Tier 1 (Transient Alarms & Auto-Resolve):**
  If an alert represents a transient ping drop, automated backup retry, or routine status check, run diagnostics using `execute_ping_diagnostic` and update the ticket silently using `log_ticket_action` with status "RESOLVED" or "MONITORING". DO NOT ping or alert the technician.

- **Tier 2 (General Inquiries & Non-Urgent Tasks):**
  If a customer submits a routine request, quote inquiry, or non-critical ticket, query the runbook via `query_client_runbook` and log the drafted diagnostic response into DynamoDB using `log_ticket_action` with status "QUEUED_DRAFT". DO NOT escalate.

- **Tier 3 (Approaching SLA Breach):**
  If a ticket is within 25% of its contract SLA expiration window without resolution, prepare the recommended response and invoke `escalate_to_technician` with Urgency Level "TIER_3_SLA_WARNING".

- **Tier 4 (Critical Site Outage / Physical Dispatch):**
  If core network infrastructure is confirmed unreachable, an on-site hardware swap is required, or a high-dollar billable action is requested:
  1. Retrieve client address and network layout with `query_client_runbook`.
  2. Confirm status with `execute_ping_diagnostic`.
  3. Immediately invoke `escalate_to_technician` with Urgency Level "TIER_4_IMMEDIATE_DISPATCH" and include complete site location, diagnostic findings, and recommended spare parts.

Operate decisively. Provide compact, structured reasoning steps before tool invocation.
"""


def create_dispatch_agent() -> Agent:
    """Initializes and returns the Strands Agent configured with Amazon Bedrock

    and Go-Dispatch tools.
    """
    logger.info(
        f"Initializing Strands Agent with Bedrock model: {settings.bedrock_model_id} (Region: {settings.aws_region})"
    )

    # Configure Amazon Bedrock backend
    llm_backend = BedrockModel(
        model_id=settings.bedrock_model_id,
        region_name=settings.aws_region,
        temperature=0.1,  # Low temperature for deterministic operational decisions
        max_tokens=2048,
    )

    # Register the agent tools
    agent_tools = [
        query_client_runbook,
        execute_ping_diagnostic,
        log_ticket_action,
        escalate_to_technician,
    ]

    # Instantiate Strands Agent
    agent = Agent(
        model=llm_backend,
        system_prompt=SYSTEM_PROMPT,
        tools=agent_tools,
    )

    return agent


class DispatchOrchestrator:
    """Manages event ingestion and passes structured operational telemetry

    into the Strands agent loop.
    """

    def __init__(self):
        self.agent = create_dispatch_agent()

    def process_incident(self, incident_payload: Dict[str, Any]) -> str:
        """Runs the autonomous triage loop for an incoming ticket, alert, or webhook."""
        client_id = incident_payload.get("client_id", "UNKNOWN_CLIENT")
        client_name = incident_payload.get("client_name", "Unknown Customer")
        ticket_id = incident_payload.get("ticket_id", "TEMP-000")
        raw_alert = incident_payload.get("alert_text", "")
        sla_window_minutes = incident_payload.get("sla_window_minutes", 120)
        source = incident_payload.get("source", "webhook")

        prompt = (
            f"PROCESS INCOMING INCIDENT EVENT:\n"
            f"- Source: {source}\n"
            f"- Ticket ID: {ticket_id}\n"
            f"- Client ID: {client_id} ({client_name})\n"
            f"- Contract SLA Window: {sla_window_minutes} minutes remaining\n"
            f"- Event / Alert Details: {raw_alert}\n\n"
            f"Evaluate severity, consult runbooks or diagnostics if needed, "
            f"and execute the appropriate quiet resolution or technician escalation."
        )

        logger.info(f"Triggering Go-Dispatch Agent for Ticket {ticket_id}...")
        response = self.agent.run(prompt)
        return str(response)