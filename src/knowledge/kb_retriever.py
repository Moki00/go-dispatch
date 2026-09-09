"""
Go-Dispatch: Amazon Bedrock Knowledge Bases Retriever

Modular retrieval layer for client-specific network topology, gateway IP
schemas, SLA tiers, and standard troubleshooting runbooks.

Wraps the ``bedrock-agent-runtime`` retrieve API behind a small, injectable
class so the agent tools stay declarative. When no Knowledge Base is
configured (``BEDROCK_KB_ID`` unset) it degrades gracefully to a deterministic
mock response, which keeps the local CLI demo and hackathon offline mode fully
functional without any AWS calls.
"""

import logging
from typing import Any, List, Optional

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from src.config import get_settings

logger = logging.getLogger("go_dispatch.knowledge")

# Deterministic topology returned when no live Bedrock KB is wired up. Mirrors
# the shape of a real "Gold tier" client runbook so downstream reasoning and
# demos behave identically to the connected path.
MOCK_RUNBOOK_CONTEXT = (
    "SLA: Gold (2hr response, 4hr onsite) | Primary Gateway: 192.168.10.1 | "
    "Edge Device: UniFi Dream Machine Pro | Spare Switch: USW-24-PoE in Server Closet."
)


class KBRetriever:
    """High-level retriever over an Amazon Bedrock Knowledge Base.

    Encapsulates the vector-search retrieve call, result flattening, mock
    fallback, and AWS error handling. Accepts an optional pre-built client and
    ``kb_id`` so callers (and tests) can inject dependencies; otherwise it
    resolves both from :func:`src.config.get_settings`.
    """

    def __init__(
        self,
        client: Optional[Any] = None,
        kb_id: Optional[str] = None,
        num_results: int = 3,
    ) -> None:
        settings = get_settings()
        # boto3 clients expose operations dynamically at runtime, so the handle
        # is typed as Any to avoid spurious static "unknown attribute" warnings.
        self._client: Any = client or boto3.client(
            "bedrock-agent-runtime", region_name=settings.aws_region
        )
        # ``kb_id is None`` means "read from settings"; an explicit "" means
        # "force mock mode" (used by tests and offline demos).
        self._kb_id = kb_id if kb_id is not None else settings.bedrock_kb_id
        self._num_results = num_results

    @property
    def is_live(self) -> bool:
        """True when a real Bedrock Knowledge Base ID is configured."""
        return bool(self._kb_id)

    def retrieve(self, client_id: str, query: str) -> str:
        """Retrieve runbook context for a client as a formatted string.

        Returns the mock context when no KB is configured, a joined set of
        retrieved passages on success, a not-found message when the KB has no
        matching documents, or a descriptive error string on AWS failure.
        """
        if not self.is_live:
            return self.mock_response(client_id, query)

        try:
            passages = self._retrieve_passages(f"Client ID {client_id}: {query}")
        except (ClientError, BotoCoreError) as exc:
            logger.error(f"Error querying Bedrock KB: {exc}")
            return f"Error retrieving KB context: {str(exc)}"

        if not passages:
            return f"No runbook documentation found for client {client_id}."
        return "\n---\n".join(passages)

    def _retrieve_passages(self, text: str) -> List[str]:
        """Run the raw vector search and flatten passages to plain text."""
        response = self._client.retrieve(
            knowledgeBaseId=self._kb_id,
            retrievalQuery={"text": text},
            retrievalConfiguration={
                "vectorSearchConfiguration": {"numberOfResults": self._num_results}
            },
        )
        return [
            doc["content"]["text"] for doc in response.get("retrievalResults", [])
        ]

    @staticmethod
    def mock_response(client_id: str, query: str) -> str:
        """Deterministic runbook context for offline / unconfigured environments."""
        return (
            f"[MOCK KB] Client: {client_id} | Query: {query} | {MOCK_RUNBOOK_CONTEXT}"
        )
