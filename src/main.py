"""
Go-Dispatch: Application Entrypoint & Ingestion API
Provides FastAPI endpoints for inbound alert webhooks and an interactive
CLI test harness for local simulation.
"""

import argparse
import json
import logging
import sys
from typing import Any, Dict, Optional

import uvicorn
from fastapi import BackgroundTasks, FastAPI, HTTPException, status
from pydantic import BaseModel, Field
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from src.agent.core import DispatchOrchestrator
from src.config import get_settings

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("go_dispatch.main")
settings = get_settings()
console = Console()

# ---------------------------------------------------------------------------
# FastAPI Application & Schemas
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Go-Dispatch API",
    description="Autonomous Zero-Distraction Triage & Mobilization Agent",
    version="1.0.0",
)

orchestrator: Optional[DispatchOrchestrator] = None


@app.on_event("startup")
def startup_event():
    """Initializes the Strands Agent orchestrator on server startup."""
    global orchestrator
    logger.info("Starting up Go-Dispatch service...")
    orchestrator = DispatchOrchestrator()


class WebhookPayload(BaseModel):
    ticket_id: str = Field(..., example="TCK-9402")
    client_id: str = Field(..., example="CL-882")
    client_name: str = Field(..., example="Pendergrass Industrial Supplies")
    alert_text: str = Field(
        ...,
        example="CRITICAL: Primary gateway (192.168.10.1) unreachable. 100% packet loss.",
    )
    sla_window_minutes: int = Field(default=120, example=60)
    source: str = Field(default="monitoring_webhook", example="uptime_kuma")


class TriageResponse(BaseModel):
    status: str
    ticket_id: str
    agent_output: str


@app.get("/health", status_code=status.HTTP_200_OK)
def health_check():
    """Service health and readiness check."""
    return {"status": "healthy", "service": "go-dispatch", "region": settings.aws_region}


@app.post("/api/v1/incidents", response_model=TriageResponse, status_code=status.HTTP_202_ACCEPTED)
def ingest_incident(payload: WebhookPayload, background_tasks: BackgroundTasks):
    """Asynchronously ingests and processes an inbound monitoring alert or customer ticket."""
    if not orchestrator:
        raise HTTPException(status_code=503, detail="Dispatch orchestrator is initializing.")

    # Process through the Strands Agent loop
    result = orchestrator.process_incident(payload.model_dump())

    return TriageResponse(
        status="processed",
        ticket_id=payload.ticket_id,
        agent_output=result,
    )


# ---------------------------------------------------------------------------
# CLI Test Runner & Simulation Harness
# ---------------------------------------------------------------------------

SAMPLE_SCENARIOS = {
    "1": {
        "title": "Tier 1: Transient Ping Flap (Quiet Auto-Resolve)",
        "payload": {
            "ticket_id": "TCK-1001",
            "client_id": "CL-042",
            "client_name": "Apex Distribution Center",
            "alert_text": "WARN: Single ICMP ping dropped on secondary guest Wi-Fi VLAN 20.",
            "sla_window_minutes": 240,
            "source": "snmp_trap",
        },
    },
    "2": {
        "title": "Tier 2: Routine Software Ingestion (Silent Async Draft)",
        "payload": {
            "ticket_id": "TCK-1002",
            "client_id": "CL-019",
            "client_name": "Georgia Recycling Group",
            "alert_text": "Inquiry: Can we add 2 additional Microsoft 365 Business Standard seats to our tenant next week?",
            "sla_window_minutes": 480,
            "source": "email_ticket",
        },
    },
    "3": {
        "title": "Tier 3: Impending SLA Breach (High-Priority Alert)",
        "payload": {
            "ticket_id": "TCK-1003",
            "client_id": "CL-104",
            "client_name": "Jackson Medical Clinic",
            "alert_text": "SLA Warning: Unanswered ticket on billing sync timeout. Contract SLA expires in 15 minutes.",
            "sla_window_minutes": 15,
            "source": "sla_monitor",
        },
    },
    "4": {
        "title": "Tier 4: Catastrophic Core Switch Down (Immediate Dispatch)",
        "payload": {
            "ticket_id": "TCK-1004",
            "client_id": "CL-001",
            "client_name": "Pendergrass Logistics Hub",
            "alert_text": "EMERGENCY: Core UniFi Switch USW-24-PoE unreachable. All warehouse POS and VOIP endpoints down.",
            "sla_window_minutes": 30,
            "source": "network_sentinel",
        },
    },
}


def run_cli_simulation():
    """Interactive command-line harness to test Strands triage flows."""
    console.print(
        Panel.fit(
            "[bold green]Go-Dispatch[/bold green] - Autonomous Zero-Distraction Triage\n"
            "[italic]Built with Strands Agents SDK & Amazon Bedrock[/italic]",
            border_style="green",
        )
    )

    runner_orchestrator = DispatchOrchestrator()

    while True:
        table = Table(title="Select a Test Incident Scenario", show_header=True)
        table.add_column("Key", style="bold cyan", width=6)
        table.add_column("Scenario", style="bold white")

        for key, item in SAMPLE_SCENARIOS.items():
            table.add_row(key, item["title"])
        table.add_row("q", "Quit")

        console.print(table)
        choice = input("\nEnter selection (1-4, q): ").strip().lower()

        if choice == "q":
            console.print("[yellow]Exiting Go-Dispatch CLI.[/yellow]")
            sys.exit(0)

        if choice not in SAMPLE_SCENARIOS:
            console.print("[red]Invalid choice. Select 1-4 or q.[/red]\n")
            continue

        selected = SAMPLE_SCENARIOS[choice]
        console.print(f"\n[bold blue]Running Scenario:[/bold blue] {selected['title']}")
        console.print(
            Panel(
                json.dumps(selected["payload"], indent=2),
                title="Inbound Webhook Payload",
                border_style="blue",
            )
        )

        with console.status("[bold green]Go-Dispatch Agent reasoning and executing tools...[/bold green]"):
            response = runner_orchestrator.process_incident(selected["payload"])

        console.print(
            Panel(
                response,
                title="[bold green]Agent Execution Result[/bold green]",
                border_style="green",
            )
        )
        print("\n" + "=" * 60 + "\n")


# ---------------------------------------------------------------------------
# Execution Router
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Go-Dispatch Service & Test Harness")
    parser.add_argument(
        "--cli",
        action="store_true",
        help="Run the interactive CLI simulation harness instead of starting the FastAPI server.",
    )
    args = parser.parse_args()

    if args.cli:
        run_cli_simulation()
    else:
        console.print(f"[bold green]Starting Go-Dispatch FastAPI Server on {settings.app_host}:{settings.app_port}...[/bold green]")
        uvicorn.run(
            "src.main:app",
            host=settings.app_host,
            port=settings.app_port,
            reload=settings.debug,
        )