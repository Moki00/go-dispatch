"""
Go-Dispatch: DynamoDB Persistence Layer

Structured models and repository classes for ticket state tracking and client
SLA metadata. Wraps the boto3 DynamoDB resource behind small repositories so
the agent tools stay declarative and testable.

Every write degrades gracefully: when DynamoDB is unreachable (local demo /
hackathon offline mode, missing credentials, throttling) the repositories log
the failure and fall back to local acknowledgement rather than crashing the
agent loop.
"""

import logging
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from src.config import get_settings

logger = logging.getLogger("go_dispatch.db")


def _utc_now_iso() -> str:
    """Current UTC timestamp as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


@dataclass
class Ticket:
    """A support/incident ticket tracked in the DynamoDB tickets table.

    ``ticket_id`` is the partition key.
    """

    ticket_id: str
    status: str = "OPEN"
    last_action: str = ""
    internal_notes: str = "Action processed by Go-Dispatch Agent."
    client_id: Optional[str] = None
    updated_at: str = field(default_factory=_utc_now_iso)

    def to_item(self) -> Dict[str, Any]:
        """Serialize to a DynamoDB item, dropping ``None`` values."""
        return {k: v for k, v in asdict(self).items() if v is not None}

    @classmethod
    def from_item(cls, item: Dict[str, Any]) -> "Ticket":
        """Build a ``Ticket`` from a raw DynamoDB item, ignoring unknown keys."""
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in item.items() if k in known})


@dataclass
class Client:
    """Client SLA metadata and site topology stored in the clients table.

    ``client_id`` is the partition key. Field dossier attributes (site address,
    gateway, spare parts) back the Tier 4 field-dispatch escalation flow.
    """

    client_id: str
    client_name: str = ""
    sla_tier: str = "Standard"
    sla_window_minutes: int = 120
    site_address: str = ""
    primary_gateway: str = ""
    edge_device: str = ""
    spare_parts: str = ""

    def to_item(self) -> Dict[str, Any]:
        """Serialize to a DynamoDB item, dropping ``None`` values."""
        return {k: v for k, v in asdict(self).items() if v is not None}

    @classmethod
    def from_item(cls, item: Dict[str, Any]) -> "Client":
        """Build a ``Client`` from a raw DynamoDB item, ignoring unknown keys."""
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in item.items() if k in known})


# ---------------------------------------------------------------------------
# Repositories
# ---------------------------------------------------------------------------


class _BaseRepository:
    """Shared DynamoDB resource handling for concrete repositories."""

    def __init__(self, table_name: str, resource: Optional[Any] = None) -> None:
        settings = get_settings()
        # boto3's dynamodb resource builds .Table() dynamically at runtime, so it
        # is typed as Any to avoid spurious static "unknown attribute" warnings.
        self._resource: Any = resource or boto3.resource(
            "dynamodb", region_name=settings.aws_region
        )
        self._table_name = table_name

    @property
    def table(self) -> Any:
        """Resolve the table handle lazily.

        Resolved per access (rather than cached) so table construction errors
        surface at call time and degrade gracefully — the agent should keep
        running even when AWS is unreachable.
        """
        return self._resource.Table(self._table_name)


class TicketRepository(_BaseRepository):
    """CRUD + status-transition operations for incident tickets."""

    def __init__(
        self, resource: Optional[Any] = None, table_name: Optional[str] = None
    ) -> None:
        settings = get_settings()
        super().__init__(table_name or settings.dynamodb_tickets_table, resource)

    def update_action(
        self,
        ticket_id: str,
        action_summary: str,
        new_status: str,
        internal_notes: Optional[str] = None,
    ) -> str:
        """Record an agent action and status transition on a ticket.

        Returns a human-readable confirmation on success, or a local
        acknowledgement string when DynamoDB is unreachable so Tier 1/2
        auto-resolutions never fail the agent loop.
        """
        notes = internal_notes or "Action processed by Go-Dispatch Agent."
        try:
            self.table.update_item(
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
                    ":ua": _utc_now_iso(),
                    ":in": notes,
                },
            )
            return f"Ticket {ticket_id} updated successfully. Status: {new_status}."
        except Exception as exc:  # noqa: BLE001 - never let telemetry logging break the agent loop
            logger.warning(f"DynamoDB update skipped or failed: {exc}")
            return (
                f"Action logged locally (Ticket {ticket_id}): "
                f"{action_summary} [Status: {new_status}]"
            )

    def get(self, ticket_id: str) -> Optional[Ticket]:
        """Fetch a ticket by id, or ``None`` if missing / unreachable."""
        try:
            response = self.table.get_item(Key={"ticket_id": ticket_id})
        except (ClientError, BotoCoreError) as exc:
            logger.warning(f"DynamoDB get_item failed for {ticket_id}: {exc}")
            return None
        item = response.get("Item")
        return Ticket.from_item(item) if item else None

    def save(self, ticket: Ticket) -> bool:
        """Upsert a full ticket record. Returns ``True`` on success."""
        try:
            self.table.put_item(Item=ticket.to_item())
            return True
        except (ClientError, BotoCoreError) as exc:
            logger.warning(f"DynamoDB put_item failed for {ticket.ticket_id}: {exc}")
            return False


class ClientRepository(_BaseRepository):
    """Read/write access to client SLA metadata and site topology."""

    def __init__(
        self, resource: Optional[Any] = None, table_name: Optional[str] = None
    ) -> None:
        settings = get_settings()
        super().__init__(table_name or settings.dynamodb_clients_table, resource)

    def get(self, client_id: str) -> Optional[Client]:
        """Fetch client metadata by id, or ``None`` if missing / unreachable."""
        try:
            response = self.table.get_item(Key={"client_id": client_id})
        except (ClientError, BotoCoreError) as exc:
            logger.warning(f"DynamoDB get_item failed for {client_id}: {exc}")
            return None
        item = response.get("Item")
        return Client.from_item(item) if item else None

    def save(self, client: Client) -> bool:
        """Upsert a full client record. Returns ``True`` on success."""
        try:
            self.table.put_item(Item=client.to_item())
            return True
        except (ClientError, BotoCoreError) as exc:
            logger.warning(f"DynamoDB put_item failed for {client.client_id}: {exc}")
            return False
