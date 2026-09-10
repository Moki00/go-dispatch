"""
Tests for Go-Dispatch Agent Flow, Ingestion API, and Orchestrator (src/main.py & src/agent/core.py).
Verifies FastAPI endpoints, payload validation, scenario configurations, and agent prompt generation,
with placeholder tests for future architectural features.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
import pytest
from fastapi.testclient import TestClient
from strands.hooks import BeforeToolCallEvent, HookRegistry

from src.agent.approval import (
    ApprovalDecision,
    DispatchApprovalHook,
    auto_approve,
)
from src.agent.core import DispatchOrchestrator
from src.config import get_settings
from src.main import SAMPLE_SCENARIOS, WebhookPayload, app
from src.scheduler.sla_monitor import SLAMonitor

client = TestClient(app)
settings = get_settings()


# ===========================================================================
# 1. Tests for Settings & Configuration
# ===========================================================================

def test_settings_load_defaults():
    """Verifies that project settings load appropriate default AWS and model values."""
    assert settings.aws_region == "us-east-1"
    assert "claude-3-5-sonnet" in settings.bedrock_model_id
    assert settings.dynamodb_tickets_table == "GoDispatch_Tickets"
    assert settings.app_port == 8000


# ===========================================================================
# 2. Tests for FastAPI Endpoints & Validation
# ===========================================================================

def test_health_check_endpoint():
    """Verifies that the GET /health endpoint returns healthy status and region."""
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["service"] == "go-dispatch"
    assert data["region"] == settings.aws_region


def test_webhook_payload_model_validation():
    """Verifies Pydantic validation for incoming incident webhook payload."""
    payload_data = {
        "ticket_id": "TCK-101",
        "client_id": "CL-001",
        "client_name": "Acme Industrial",
        "alert_text": "High packet loss on gateway",
        "sla_window_minutes": 60,
        "source": "pingdom",
    }
    validated = WebhookPayload(**payload_data)
    assert validated.ticket_id == "TCK-101"
    assert validated.sla_window_minutes == 60


def test_webhook_payload_missing_required_fields():
    """Verifies that missing required fields trigger validation errors."""
    response = client.post("/api/v1/incidents", json={"source": "uptime_bot"})
    assert response.status_code == 422  # Unprocessable Entity


def test_sample_scenarios_integrity():
    """Verifies that all 4 predefined CLI scenarios (Tiers 1-4) have valid payloads."""
    assert len(SAMPLE_SCENARIOS) == 4
    for key, scenario in SAMPLE_SCENARIOS.items():
        assert "title" in scenario
        assert "payload" in scenario
        payload = scenario["payload"]
        assert "ticket_id" in payload
        assert "client_id" in payload
        assert "alert_text" in payload
        assert "sla_window_minutes" in payload


# ===========================================================================
# 3. Tests for DispatchOrchestrator & Agent Prompt Assembly
# ===========================================================================

def test_orchestrator_prompt_assembly():
    """Verifies that DispatchOrchestrator formats telemetry correctly into the prompt."""
    mock_agent = MagicMock(return_value="Action: Analyzed and resolved silently.")

    with patch("src.agent.core.create_dispatch_agent", return_value=mock_agent):
        orchestrator = DispatchOrchestrator()
        test_incident = {
            "ticket_id": "TCK-4001",
            "client_id": "CL-550",
            "client_name": "Metro Health Hospital",
            "alert_text": "Critical switch offline",
            "sla_window_minutes": 45,
            "source": "snmp_trap",
        }

        result = orchestrator.process_incident(test_incident)

        mock_agent.assert_called_once()
        sent_prompt = mock_agent.call_args[0][0]

        assert "TCK-4001" in sent_prompt
        assert "Metro Health Hospital" in sent_prompt
        assert "45 minutes remaining" in sent_prompt
        assert "Critical switch offline" in sent_prompt
        assert result == "Action: Analyzed and resolved silently."


def test_ingest_incident_api_success():
    """Verifies the complete POST /api/v1/incidents endpoint flow with a mocked orchestrator."""
    mock_orchestrator = MagicMock()
    mock_orchestrator.process_incident.return_value = "Outage verified. Critical dispatch sent."

    with patch("src.main.orchestrator", mock_orchestrator):
        payload = {
            "ticket_id": "TCK-8800",
            "client_id": "CL-010",
            "client_name": "Southeast Distribution",
            "alert_text": "Core gateway unresponsive",
            "sla_window_minutes": 30,
            "source": "monitoring_webhook",
        }
        response = client.post("/api/v1/incidents", json=payload)

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "processed"
        assert data["ticket_id"] == "TCK-8800"
        assert "Outage verified" in data["agent_output"]


# ===========================================================================
# 4. Placeholder Tests for Future Architectural Features (Skipped)
# ===========================================================================

# ===========================================================================
# 4. Human-in-the-Loop Dispatch Approval Gate (Strands native hooks)
# ===========================================================================

def _make_before_tool_event(tool_name: str, tool_input: dict) -> BeforeToolCallEvent:
    """Build a BeforeToolCallEvent as Strands would just before running a tool."""
    return BeforeToolCallEvent(
        agent=MagicMock(name="agent"),
        selected_tool=MagicMock(name="selected_tool"),
        tool_use={
            "toolUseId": "tu-test-1",
            "name": tool_name,
            "input": tool_input,
        },
        invocation_state={},
    )


def _dispatch_input() -> dict:
    return {
        "urgency_level": "TIER_4_IMMEDIATE_DISPATCH",
        "client_name": "Pendergrass Logistics Hub",
        "issue_summary": "Core switch down",
        "site_address": "500 Depot Rd",
        "recommended_action": "Swap USW-24-PoE",
        "sla_deadline_minutes": 30,
    }


def test_human_in_the_loop_approval_flow():
    """Physical dispatch is gated: denied by default, allowed on approval, and
    the gate never touches non-dispatch tools — verified through the real
    Strands hook-dispatch path."""
    registry = HookRegistry()

    # --- Denied by default: escalate_to_technician is held for approval ---
    deny_hook = DispatchApprovalHook()  # default policy = deny_by_default
    deny_hook.register_hooks(registry)

    blocked = _make_before_tool_event("escalate_to_technician", _dispatch_input())
    registry.invoke_callbacks(blocked)

    assert isinstance(blocked.cancel_tool, str)
    assert "APPROVAL" in blocked.cancel_tool.upper()
    assert deny_hook.held_count == 1
    assert deny_hook.audit_log[-1]["approved"] is False
    assert deny_hook.audit_log[-1]["client_name"] == "Pendergrass Logistics Hub"

    # --- Approved: the same call proceeds (cancel_tool stays False) ---
    approve_registry = HookRegistry()
    approve_hook = DispatchApprovalHook(approver=auto_approve)
    approve_hook.register_hooks(approve_registry)

    allowed = _make_before_tool_event("escalate_to_technician", _dispatch_input())
    approve_registry.invoke_callbacks(allowed)

    assert allowed.cancel_tool is False
    assert approve_hook.held_count == 0
    assert approve_hook.audit_log[-1]["approved"] is True

    # --- Non-dispatch tools are never gated ---
    passthrough = _make_before_tool_event(
        "log_ticket_action", {"ticket_id": "TCK-1", "new_status": "RESOLVED"}
    )
    registry.invoke_callbacks(passthrough)
    assert passthrough.cancel_tool is False
    assert deny_hook.held_count == 1  # unchanged; no new audit entry

    # --- A custom approver decision is honored ---
    custom = DispatchApprovalHook(
        approver=lambda req: ApprovalDecision(
            approved=False, reason="After-hours dispatch requires manager sign-off.", approver="on_call_lead"
        )
    )
    custom_registry = HookRegistry()
    custom.register_hooks(custom_registry)
    ev = _make_before_tool_event("escalate_to_technician", _dispatch_input())
    custom_registry.invoke_callbacks(ev)
    assert "manager sign-off" in ev.cancel_tool
    assert custom.audit_log[-1]["approver"] == "on_call_lead"


def test_approval_hook_wired_into_orchestrator():
    """DispatchOrchestrator installs the approval hook into the agent it builds."""
    hook = DispatchApprovalHook()
    with patch("src.agent.core.create_dispatch_agent", return_value=MagicMock()) as mock_create:
        DispatchOrchestrator(approval_hook=hook)
        mock_create.assert_called_once_with(hooks=[hook])


# ===========================================================================
# 5. Autonomous SLA Countdown Daemon
# ===========================================================================

class _FakeClock:
    """Deterministic, advanceable clock for SLA countdown tests."""

    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, minutes: float) -> None:
        self.now += timedelta(minutes=minutes)


def test_autonomous_sla_tracking_daemon():
    """The monitor proactively warns a ticket once it drops below the SLA
    threshold, warns exactly once, ignores tickets still in the safe zone, and
    stops tracking resolved tickets."""
    clock = _FakeClock(datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc))
    warned_ids = []
    monitor = SLAMonitor(
        warn_threshold=0.25,
        on_warning=lambda ticket, frac: warned_ids.append(ticket.ticket_id),
        clock=clock,
    )

    # Ticket A: 60-minute window. Ticket B (control): 100-minute window.
    monitor.register("TCK-A", "Acme Corp", sla_window_minutes=60)
    monitor.register("TCK-B", "Globex", sla_window_minutes=100)

    # t0 — both fresh, nothing warned.
    assert monitor.check() == []
    assert monitor.remaining_minutes("TCK-A") == pytest.approx(60.0)

    # +50m — A has 10m (16.7%) left => breach threshold; B has 50m (50%) left.
    clock.advance(50)
    newly = monitor.check()
    assert [t.ticket_id for t in newly] == ["TCK-A"]
    assert warned_ids == ["TCK-A"]

    # Idempotent: re-checking at the same instant does not re-warn.
    assert monitor.check() == []
    assert warned_ids == ["TCK-A"]

    # Resolving a ticket removes it from the countdown entirely.
    monitor.resolve("TCK-A")
    assert "TCK-A" not in monitor.active_ticket_ids

    # +45m more (t0+95m) — B now has 5m (5%) left => it finally warns.
    clock.advance(45)
    newly = monitor.check()
    assert [t.ticket_id for t in newly] == ["TCK-B"]
    assert warned_ids == ["TCK-A", "TCK-B"]


def test_sla_daemon_async_loop_fires_warning():
    """The async run() loop performs a countdown check and fires warnings, then
    shuts down cleanly on the stop event."""
    clock = _FakeClock(datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc))
    warned_ids = []
    monitor = SLAMonitor(
        warn_threshold=0.25,
        on_warning=lambda ticket, frac: warned_ids.append(ticket.ticket_id),
        clock=clock,
    )
    # Already past the threshold at registration (2m of a 60m window remaining).
    monitor.register(
        "TCK-URGENT",
        "Metro Health",
        sla_window_minutes=60,
        started_at=clock.now - timedelta(minutes=58),
    )

    async def _drive():
        stop = asyncio.Event()
        task = asyncio.create_task(monitor.run(interval_seconds=0.01, stop_event=stop))
        # run() checks immediately on entry, before its first await.
        await asyncio.sleep(0.05)
        stop.set()
        await asyncio.wait_for(task, timeout=1.0)

    asyncio.run(_drive())
    assert warned_ids == ["TCK-URGENT"]


# ===========================================================================
# 6. Asynchronous Background Webhook Ingestion
# ===========================================================================

def test_async_background_webhook_ingestion():
    """POST /api/v1/incidents/async acknowledges immediately (202) and processes
    the incident off the request path, with the result observable via the ledger."""
    import src.main as main_module

    mock_orchestrator = MagicMock()
    mock_orchestrator.process_incident.return_value = "Outage verified. Critical dispatch sent."

    main_module.ingestion_status.clear()

    with patch("src.main.orchestrator", mock_orchestrator):
        payload = {
            "ticket_id": "TCK-ASYNC-1",
            "client_id": "CL-777",
            "client_name": "Nightshift Logistics",
            "alert_text": "Core gateway unresponsive",
            "sla_window_minutes": 30,
            "source": "monitoring_webhook",
        }
        response = client.post("/api/v1/incidents/async", json=payload)

        # Immediate, non-blocking acknowledgement.
        assert response.status_code == 202
        data = response.json()
        assert data["status"] == "accepted"
        assert data["ticket_id"] == "TCK-ASYNC-1"

        # TestClient drains BackgroundTasks as part of the response cycle, so the
        # agent loop has now run exactly once — off the request handler.
        mock_orchestrator.process_incident.assert_called_once()
        assert mock_orchestrator.process_incident.call_args[0][0]["ticket_id"] == "TCK-ASYNC-1"

        # The ledger reflects the completed background processing.
        status_resp = client.get("/api/v1/incidents/TCK-ASYNC-1/status")
        assert status_resp.status_code == 200
        record = status_resp.json()
        assert record["status"] == "PROCESSED"
        assert "Outage verified" in record["agent_output"]


def test_async_ingestion_status_unknown_ticket_404():
    """Polling an unknown ticket returns 404."""
    response = client.get("/api/v1/incidents/DOES-NOT-EXIST/status")
    assert response.status_code == 404
