"""
Tests for Go-Dispatch Agent Flow, Ingestion API, and Orchestrator (src/main.py & src/agent/core.py).
Verifies FastAPI endpoints, payload validation, scenario configurations, and agent prompt generation,
with placeholder tests for future architectural features.
"""

from unittest.mock import MagicMock, patch
import pytest
from fastapi.testclient import TestClient

from src.agent.core import DispatchOrchestrator
from src.config import get_settings
from src.main import SAMPLE_SCENARIOS, WebhookPayload, app

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

@pytest.mark.skip(reason="Yet to be implemented: Strands native Human-in-the-Loop approval before physical dispatch")
def test_human_in_the_loop_approval_flow():
    """Future verification for approval gate before physical technician dispatch."""
    pass


@pytest.mark.skip(reason="Yet to be implemented: Autonomous SLA countdown timer & proactive warning scheduler")
def test_autonomous_sla_tracking_daemon():
    """Future verification for proactive countdown monitoring on active tickets."""
    pass


@pytest.mark.skip(reason="Yet to be implemented: Asynchronous background event queue / PubSub ingestion in FastAPI")
def test_async_background_webhook_ingestion():
    """Future verification for async non-blocking webhook ingestion."""
    pass
