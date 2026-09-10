"""
Go-Dispatch: Application Entrypoint & Ingestion API
Provides FastAPI endpoints for inbound alert webhooks and an interactive CLI test harness.
"""

import argparse
import asyncio
from contextlib import asynccontextmanager
import json
import logging
import sys
from typing import Any, Dict, Optional

from fastapi import BackgroundTasks, FastAPI, HTTPException, status
from pydantic import BaseModel, Field
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
import uvicorn

from src.agent.core import DispatchOrchestrator
from src.config import get_settings
from src.scheduler.sla_monitor import SLAMonitor, TrackedTicket

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("go_dispatch.main")
settings = get_settings()
console = Console()

# Global service singletons, initialized by the FastAPI lifespan.
orchestrator: Optional[DispatchOrchestrator] = None
sla_monitor: Optional[SLAMonitor] = None
_sla_stop_event: Optional[asyncio.Event] = None
_sla_task: Optional["asyncio.Task[None]"] = None

# In-memory ledger tracking the state of asynchronously ingested incidents so the
# non-blocking webhook path stays observable (QUEUED -> PROCESSING -> PROCESSED/FAILED).
ingestion_status: Dict[str, Dict[str, Any]] = {}


def _log_sla_warning(ticket: TrackedTicket, fraction: float) -> None:
    """Default SLA countdown reaction: log a Tier-3 warning for the operator."""
    logger.warning(
        f"[SLA COUNTDOWN] Ticket {ticket.ticket_id} ({ticket.client_name}) crossed the "
        f"SLA warning threshold — {fraction * 100:.0f}% of window remaining; "
        f"deadline {ticket.deadline().isoformat()}. Tier-3 escalation recommended."
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    global orchestrator, sla_monitor, _sla_stop_event, _sla_task
    logger.info("Starting up Go-Dispatch service...")
    orchestrator = DispatchOrchestrator()

    # Launch the autonomous SLA countdown daemon.
    sla_monitor = SLAMonitor(
        warn_threshold=settings.sla_warn_threshold,
        on_warning=_log_sla_warning,
    )
    _sla_stop_event = asyncio.Event()
    _sla_task = asyncio.create_task(
        sla_monitor.run(
            interval_seconds=settings.sla_poll_interval_seconds,
            stop_event=_sla_stop_event,
        )
    )
    logger.info("Autonomous SLA countdown daemon started.")

    yield

    logger.info("Shutting down Go-Dispatch service...")
    if _sla_stop_event is not None:
        _sla_stop_event.set()
    if _sla_task is not None:
        try:
            await asyncio.wait_for(_sla_task, timeout=5.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            _sla_task.cancel()


app = FastAPI(
    title="Go-Dispatch API",
    description="Autonomous Zero-Distraction Triage & Mobilization Agent",
    version="1.0.0",
    lifespan=lifespan,
)


class WebhookPayload(BaseModel):
    ticket_id: str = Field(..., examples=["TCK-9402"])
    client_id: str = Field(..., examples=["CL-882"])
    client_name: str = Field(..., examples=["Pendergrass Industrial Supplies"])
    alert_text: str = Field(
        ...,
        examples=["CRITICAL: Primary gateway (192.168.10.1) unreachable. 100% packet loss."],
    )
    sla_window_minutes: int = Field(default=120, examples=[60])
    source: str = Field(default="monitoring_webhook", examples=["uptime_kuma"])


class TriageResponse(BaseModel):
    status: str
    ticket_id: str
    agent_output: str


class IngestionAck(BaseModel):
    status: str
    ticket_id: str
    detail: str


@app.get("/health", status_code=status.HTTP_200_OK)
def health_check():
    return {"status": "healthy", "service": "go-dispatch", "region": settings.aws_region}


@app.post("/api/v1/incidents", response_model=TriageResponse, status_code=status.HTTP_200_OK)
def ingest_incident(payload: WebhookPayload):
    """Synchronous triage: blocks until the agent loop completes and returns its output."""
    if not orchestrator:
        raise HTTPException(status_code=503, detail="Dispatch orchestrator is initializing.")

    result = orchestrator.process_incident(payload.model_dump())
    return TriageResponse(
        status="processed",
        ticket_id=payload.ticket_id,
        agent_output=result,
    )


def _process_incident_task(payload: Dict[str, Any]) -> None:
    """Background worker: runs the agent triage loop off the HTTP request path.

    Updates :data:`ingestion_status` as the incident moves through the queue so
    the async result can be polled, and releases the ticket from the SLA
    countdown once it has been handled.
    """
    ticket_id = payload.get("ticket_id", "TEMP-000")
    ingestion_status[ticket_id] = {"status": "PROCESSING", "ticket_id": ticket_id, "agent_output": None}
    try:
        if orchestrator is None:
            raise RuntimeError("Dispatch orchestrator is not initialized.")
        result = orchestrator.process_incident(payload)
        ingestion_status[ticket_id] = {
            "status": "PROCESSED",
            "ticket_id": ticket_id,
            "agent_output": str(result),
        }
    except Exception as exc:  # noqa: BLE001 - surface failure in the ledger, never crash the worker
        logger.error(f"Background triage failed for {ticket_id}: {exc}")
        ingestion_status[ticket_id] = {
            "status": "FAILED",
            "ticket_id": ticket_id,
            "agent_output": str(exc),
        }
    finally:
        if sla_monitor is not None:
            sla_monitor.resolve(ticket_id)


@app.post(
    "/api/v1/incidents/async",
    response_model=IngestionAck,
    status_code=status.HTTP_202_ACCEPTED,
)
def ingest_incident_async(payload: WebhookPayload, background_tasks: BackgroundTasks):
    """Non-blocking triage: acknowledges immediately (202) and processes in the background.

    The incident is registered with the autonomous SLA countdown daemon and
    handed to a background worker, so high-volume monitoring webhooks never block
    on the agent's reasoning loop.
    """
    if not orchestrator:
        raise HTTPException(status_code=503, detail="Dispatch orchestrator is initializing.")

    ticket_id = payload.ticket_id
    if sla_monitor is not None:
        sla_monitor.register(
            ticket_id,
            payload.client_name,
            payload.sla_window_minutes,
            issue_summary=payload.alert_text,
        )

    ingestion_status[ticket_id] = {"status": "QUEUED", "ticket_id": ticket_id, "agent_output": None}
    background_tasks.add_task(_process_incident_task, payload.model_dump())

    return IngestionAck(
        status="accepted",
        ticket_id=ticket_id,
        detail="Incident queued for background triage.",
    )


@app.get("/api/v1/incidents/{ticket_id}/status", status_code=status.HTTP_200_OK)
def get_incident_status(ticket_id: str):
    """Poll the state of an asynchronously ingested incident."""
    record = ingestion_status.get(ticket_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"No ingestion record for ticket {ticket_id}.")
    return record


# ---------------------------------------------------------------------------
# CLI Test Runner
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