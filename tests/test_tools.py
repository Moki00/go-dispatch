"""
Tests for Go-Dispatch Agent Tools Suite (src/agent/tools.py).
Verifies current tool implementations with unit tests and mocks,
and provides placeholder tests for features yet to be implemented.
"""

import json
from unittest.mock import MagicMock, patch
import pytest
from botocore.exceptions import ClientError

from src.agent.tools import (
    escalate_to_technician,
    execute_ping_diagnostic,
    log_ticket_action,
    query_client_runbook,
)
from src.db.dynamodb import Client, ClientRepository, Ticket, TicketRepository
from src.knowledge.kb_retriever import KBRetriever


# ===========================================================================
# 1. Tests for query_client_runbook
# ===========================================================================

def test_query_client_runbook_fallback_mock():
    """Verifies that query_client_runbook returns mock topology data when no Bedrock KB ID is configured."""
    with patch("src.agent.tools.settings.bedrock_kb_id", ""):
        result = query_client_runbook("CL-001", "core switch gateway")
        assert "[MOCK KB]" in result
        assert "CL-001" in result
        assert "Primary Gateway: 192.168.10.1" in result


def test_query_client_runbook_with_bedrock_retrieve():
    """Verifies that query_client_runbook formats results from Bedrock Agent Runtime when KB ID is present."""
    mock_bedrock_response = {
        "retrievalResults": [
            {"content": {"text": "Runbook step 1: Check primary power supply on rack A."}},
            {"content": {"text": "Runbook step 2: Verify optical link light on SFP+ port 24."}},
        ]
    }
    with patch("src.agent.tools.settings.bedrock_kb_id", "kb-test-12345"):
        with patch("src.agent.tools.bedrock_agent_runtime.retrieve", return_value=mock_bedrock_response) as mock_retrieve:
            result = query_client_runbook("CL-001", "rack power lights")
            mock_retrieve.assert_called_once()
            assert "Runbook step 1" in result
            assert "Runbook step 2" in result
            assert "---" in result


def test_query_client_runbook_handles_client_error():
    """Verifies graceful error handling when Bedrock KB API returns an AWS ClientError."""
    error_response = {"Error": {"Code": "AccessDeniedException", "Message": "KB Access Denied"}}
    client_error = ClientError(error_response, "Retrieve")

    with patch("src.agent.tools.settings.bedrock_kb_id", "kb-test-12345"):
        with patch("src.agent.tools.bedrock_agent_runtime.retrieve", side_effect=client_error):
            result = query_client_runbook("CL-001", "test query")
            assert "Error retrieving KB context" in result
            assert "AccessDeniedException" in result


# ===========================================================================
# 2. Tests for execute_ping_diagnostic
# ===========================================================================

def test_execute_ping_diagnostic_localhost_reachable():
    """Verifies that execute_ping_diagnostic successfully pings localhost (127.0.0.1) and detects REACHABLE status."""
    result_raw = execute_ping_diagnostic("127.0.0.1", count=2, timeout_ms=1000)
    data = json.loads(result_raw)

    assert data["target"] == "127.0.0.1"
    assert data["probes_sent"] == 2
    assert data["probes_received"] >= 1
    assert data["packet_loss_pct"] == 0.0
    assert data["status"] == "REACHABLE"
    assert "0% packet loss" in data["diagnostic_verdict"]


def test_execute_ping_diagnostic_unreachable_target():
    """Verifies that execute_ping_diagnostic detects UNREACHABLE on non-routable TEST-NET-1 (192.0.2.1)."""
    result_raw = execute_ping_diagnostic("192.0.2.1", count=1, timeout_ms=500)
    data = json.loads(result_raw)

    assert data["target"] == "192.0.2.1"
    assert data["probes_sent"] == 1
    assert data["packet_loss_pct"] == 100.0
    assert data["status"] == "UNREACHABLE"
    assert "Confirmed hard down failure" in data["diagnostic_verdict"]


def test_execute_ping_diagnostic_security_rejection():
    """Verifies that command injection or malicious target strings are rejected without running shell commands."""
    result_raw = execute_ping_diagnostic("127.0.0.1; whoami", count=1)
    data = json.loads(result_raw)

    assert data["status"] == "ERROR"
    assert "Invalid target format" in data["error"]
    assert data["packet_loss_pct"] == 100.0


def test_execute_ping_diagnostic_mocked_degraded_state():
    """Verifies that intermittent packet loss correctly sets status to DEGRADED."""
    mock_stdout = (
        "Ping statistics for 192.168.1.1:\n"
        "    Packets: Sent = 4, Received = 2, Lost = 2 (50% loss),\n"
        "Approximate round trip times in milli-seconds:\n"
        "    Minimum = 12ms, Maximum = 45ms, Average = 28ms\n"
    )
    mock_proc = MagicMock(stdout=mock_stdout, stderr="", returncode=0)

    with patch("subprocess.run", return_value=mock_proc):
        result_raw = execute_ping_diagnostic("192.168.1.1", count=4)
        data = json.loads(result_raw)

        assert data["status"] == "DEGRADED"
        assert data["packet_loss_pct"] == 50.0
        assert data["avg_rtt_ms"] == 28.0
        assert "Intermittent connectivity detected" in data["diagnostic_verdict"]


# ===========================================================================
# 3. Tests for log_ticket_action
# ===========================================================================

def test_log_ticket_action_fallback_when_dynamodb_unreachable():
    """Verifies that log_ticket_action falls back to local logging when DynamoDB is unreachable."""
    with patch("src.agent.tools.dynamodb.Table", side_effect=Exception("DynamoDB connection refused")):
        result = log_ticket_action(
            ticket_id="TCK-9999",
            action_summary="Auto-resolved transient ping alert.",
            new_status="RESOLVED",
        )
        assert "Action logged locally (Ticket TCK-9999)" in result
        assert "RESOLVED" in result


def test_log_ticket_action_dynamodb_success():
    """Verifies that log_ticket_action properly calls update_item on DynamoDB when accessible."""
    mock_table = MagicMock()
    with patch("src.agent.tools.dynamodb.Table", return_value=mock_table):
        result = log_ticket_action(
            ticket_id="TCK-1001",
            action_summary="Queued draft response for billing inquiry.",
            new_status="QUEUED_DRAFT",
            internal_notes="Reviewed against standard pricing schedule.",
        )
        mock_table.update_item.assert_called_once()
        call_kwargs = mock_table.update_item.call_args.kwargs
        assert call_kwargs["Key"] == {"ticket_id": "TCK-1001"}
        assert call_kwargs["ExpressionAttributeValues"][":status"] == "QUEUED_DRAFT"
        assert "Ticket TCK-1001 updated successfully." in result


# ===========================================================================
# 4. Tests for escalate_to_technician
# ===========================================================================

def test_escalate_to_technician_payload_structure():
    """Verifies that escalate_to_technician returns a properly structured dispatch dossier."""
    result = escalate_to_technician(
        urgency_level="TIER_4_IMMEDIATE_DISPATCH",
        client_name="Pendergrass Logistics Hub",
        issue_summary="Core switch hardware failure.",
        site_address="100 Logistics Way, Pendergrass GA",
        recommended_action="Replace USW-24-PoE with closet spare.",
        sla_deadline_minutes=25,
    )

    assert "CRITICAL ALERT DISPATCHED TO TECHNICIAN" in result
    assert "TIER_4_IMMEDIATE_DISPATCH" in result
    assert "Pendergrass Logistics Hub" in result
    assert "100 Logistics Way" in result
    assert "25 mins" in result


def test_escalate_to_technician_publishes_to_sns():
    """Verifies that escalate_to_technician triggers Amazon SNS publish when topic ARN is set."""
    with patch("src.agent.tools.settings.sns_dispatch_topic_arn", "arn:aws:sns:us-east-1:123456789012:CriticalAlerts"):
        with patch("src.agent.tools.sns_client.publish") as mock_sns_publish:
            escalate_to_technician(
                urgency_level="TIER_3_SLA_WARNING",
                client_name="Apex Healthcare",
                issue_summary="Impending SLA breach in 12 minutes.",
                site_address="450 Hospital Pkwy",
                recommended_action="Acknowledge ticket immediately.",
                sla_deadline_minutes=12,
            )
            mock_sns_publish.assert_called_once()
            call_kwargs = mock_sns_publish.call_args.kwargs
            assert call_kwargs["TopicArn"] == "arn:aws:sns:us-east-1:123456789012:CriticalAlerts"
            assert "Apex Healthcare" in call_kwargs["Subject"]
            assert "12 min(s)" in call_kwargs["Message"]


# ===========================================================================
# 5. Tests for Modular KBRetriever (src/knowledge/kb_retriever.py)
# ===========================================================================

def test_kb_retriever_mock_when_unconfigured():
    """Verifies KBRetriever returns deterministic mock topology when no KB is configured."""
    retriever = KBRetriever(kb_id="")
    assert retriever.is_live is False
    result = retriever.retrieve("CL-777", "gateway topology")
    assert "[MOCK KB]" in result
    assert "CL-777" in result
    assert "Primary Gateway: 192.168.10.1" in result


def test_kb_retriever_live_retrieve_flattens_passages():
    """Verifies KBRetriever calls Bedrock and joins retrieved passages when a KB ID is present."""
    mock_client = MagicMock()
    mock_client.retrieve.return_value = {
        "retrievalResults": [
            {"content": {"text": "Passage A: verify rack power."}},
            {"content": {"text": "Passage B: check SFP+ optical link."}},
        ]
    }
    retriever = KBRetriever(client=mock_client, kb_id="kb-abc-123")
    assert retriever.is_live is True

    result = retriever.retrieve("CL-001", "power lights")
    mock_client.retrieve.assert_called_once()
    assert "Passage A" in result
    assert "Passage B" in result
    assert "---" in result


def test_kb_retriever_empty_results_message():
    """Verifies KBRetriever reports a not-found message when the KB returns no passages."""
    mock_client = MagicMock()
    mock_client.retrieve.return_value = {"retrievalResults": []}
    retriever = KBRetriever(client=mock_client, kb_id="kb-abc-123")

    result = retriever.retrieve("CL-404", "unknown query")
    assert "No runbook documentation found for client CL-404" in result


def test_kb_retriever_handles_client_error():
    """Verifies KBRetriever gracefully surfaces AWS ClientErrors as an error string."""
    error = ClientError(
        {"Error": {"Code": "AccessDeniedException", "Message": "KB Access Denied"}},
        "Retrieve",
    )
    mock_client = MagicMock()
    mock_client.retrieve.side_effect = error
    retriever = KBRetriever(client=mock_client, kb_id="kb-abc-123")

    result = retriever.retrieve("CL-001", "test query")
    assert "Error retrieving KB context" in result
    assert "AccessDeniedException" in result


# ===========================================================================
# 6. Tests for DynamoDB Repository Layer (src/db/dynamodb.py)
# ===========================================================================

def _mock_resource_returning(table: MagicMock) -> MagicMock:
    """Helper: a DynamoDB resource whose .Table(name) yields the given table mock."""
    resource = MagicMock()
    resource.Table.return_value = table
    return resource


def test_ticket_repository_update_action_success():
    """Verifies TicketRepository.update_action issues the expected DynamoDB update."""
    mock_table = MagicMock()
    repo = TicketRepository(resource=_mock_resource_returning(mock_table), table_name="Tickets")

    result = repo.update_action("TCK-1", "Auto-resolved transient ping.", "RESOLVED")

    mock_table.update_item.assert_called_once()
    kwargs = mock_table.update_item.call_args.kwargs
    assert kwargs["Key"] == {"ticket_id": "TCK-1"}
    assert kwargs["ExpressionAttributeValues"][":status"] == "RESOLVED"
    assert "Ticket TCK-1 updated successfully." in result


def test_ticket_repository_update_action_local_fallback():
    """Verifies update_action falls back to local logging when DynamoDB is unreachable."""
    resource = MagicMock()
    resource.Table.side_effect = Exception("DynamoDB connection refused")
    repo = TicketRepository(resource=resource, table_name="Tickets")

    result = repo.update_action("TCK-2", "Queued draft.", "MONITORING")
    assert "Action logged locally (Ticket TCK-2)" in result
    assert "MONITORING" in result


def test_ticket_repository_get_returns_model():
    """Verifies TicketRepository.get hydrates a Ticket model and ignores unknown columns."""
    mock_table = MagicMock()
    mock_table.get_item.return_value = {
        "Item": {"ticket_id": "TCK-3", "status": "OPEN", "unmapped_column": "ignored"}
    }
    repo = TicketRepository(resource=_mock_resource_returning(mock_table), table_name="Tickets")

    ticket = repo.get("TCK-3")
    assert isinstance(ticket, Ticket)
    assert ticket.ticket_id == "TCK-3"
    assert ticket.status == "OPEN"


def test_ticket_repository_get_missing_returns_none():
    """Verifies TicketRepository.get returns None when the item does not exist."""
    mock_table = MagicMock()
    mock_table.get_item.return_value = {}
    repo = TicketRepository(resource=_mock_resource_returning(mock_table), table_name="Tickets")

    assert repo.get("TCK-MISSING") is None


def test_ticket_repository_save_puts_item():
    """Verifies TicketRepository.save serializes the model and calls put_item."""
    mock_table = MagicMock()
    repo = TicketRepository(resource=_mock_resource_returning(mock_table), table_name="Tickets")

    ok = repo.save(Ticket(ticket_id="TCK-4", status="RESOLVED", client_id="CL-9"))
    assert ok is True
    mock_table.put_item.assert_called_once()
    item = mock_table.put_item.call_args.kwargs["Item"]
    assert item["ticket_id"] == "TCK-4"
    assert item["status"] == "RESOLVED"
    assert item["client_id"] == "CL-9"


def test_client_repository_get_returns_model():
    """Verifies ClientRepository.get hydrates a Client model from a DynamoDB item."""
    mock_table = MagicMock()
    mock_table.get_item.return_value = {
        "Item": {
            "client_id": "CL-001",
            "client_name": "Pendergrass Logistics Hub",
            "sla_tier": "Gold",
            "site_address": "100 Logistics Way, Pendergrass GA",
        }
    }
    repo = ClientRepository(resource=_mock_resource_returning(mock_table), table_name="Clients")

    client = repo.get("CL-001")
    assert isinstance(client, Client)
    assert client.client_name == "Pendergrass Logistics Hub"
    assert client.sla_tier == "Gold"
    assert "Pendergrass GA" in client.site_address


def test_ticket_model_item_roundtrip():
    """Verifies Ticket.to_item / from_item round-trips and drops None fields."""
    ticket = Ticket(ticket_id="TCK-9", status="QUEUED_DRAFT", client_id="CL-2")
    item = ticket.to_item()
    assert item["ticket_id"] == "TCK-9"
    assert item["status"] == "QUEUED_DRAFT"
    assert "updated_at" in item  # populated by default factory

    restored = Ticket.from_item({**item, "stray_attribute": "ignored"})
    assert restored.ticket_id == "TCK-9"
    assert restored.client_id == "CL-2"


def test_client_model_drops_none_values():
    """Verifies Client.to_item excludes unset (None) values while keeping defaults."""
    item = Client(client_id="CL-5").to_item()
    assert item["client_id"] == "CL-5"
    assert item["sla_window_minutes"] == 120  # int default retained
    assert all(v is not None for v in item.values())
