"""
Go-Dispatch: Human-in-the-Loop Dispatch Approval Gate

A native Strands lifecycle hook that intercepts physical-dispatch tool calls
*before* they execute and holds them for human approval.

The agent's ``escalate_to_technician`` tool physically mobilizes a person (and
often authorizes billable work) via an SNS page. Firing it fully autonomously is
exactly the kind of action a human should sign off on first. This module wraps
the Strands ``BeforeToolCallEvent`` hook: when the agent tries to dispatch, the
gate consults an approval policy. If the dispatch is not approved, the tool call
is cancelled via ``cancel_tool`` — Strands turns it into an error-status tool
result instead of running the tool, so no technician is ever paged — and the
agent is told the dispatch is awaiting operator sign-off.

The approval policy is injectable, so the same gate serves:
- autonomous / offline demos (default: deny -> hold, fail-safe),
- an operator UI or CLI prompt (callback returns a real human decision),
- automated tests (auto-approve / auto-deny).
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence

from strands.hooks import BeforeToolCallEvent, HookProvider, HookRegistry

logger = logging.getLogger("go_dispatch.approval")

# Tools that physically mobilize a human / incur a billable action, and so must
# not run without explicit sign-off.
DEFAULT_GUARDED_TOOLS = ("escalate_to_technician",)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class ApprovalRequest:
    """A pending request to run a guarded (physical-dispatch) tool."""

    tool_name: str
    tool_input: Dict[str, Any]
    tool_use_id: str
    requested_at: datetime = field(default_factory=_utc_now)


@dataclass
class ApprovalDecision:
    """The outcome of an approval policy for a single dispatch request."""

    approved: bool
    reason: str = ""
    approver: str = "policy"


# An approver maps a request to a decision.
Approver = Callable[[ApprovalRequest], ApprovalDecision]


def deny_by_default(request: ApprovalRequest) -> ApprovalDecision:
    """Fail-safe policy: never mobilize a technician without explicit sign-off."""
    return ApprovalDecision(
        approved=False,
        reason="Physical dispatch requires human approval (no approver configured).",
        approver="default_policy",
    )


def auto_approve(request: ApprovalRequest) -> ApprovalDecision:
    """Permissive policy: approve every dispatch (trusted / automated contexts only)."""
    return ApprovalDecision(approved=True, reason="Auto-approved.", approver="auto")


class DispatchApprovalHook(HookProvider):
    """Strands hook that gates physical-dispatch tools behind human approval.

    Registers a callback on ``BeforeToolCallEvent``. For any *guarded* tool it
    consults the configured approver; on denial it sets ``event.cancel_tool`` so
    Strands returns that message as an error-status tool result (the technician is
    never paged) and the agent learns the dispatch is awaiting approval. Every
    decision is recorded in :attr:`audit_log` for observability.
    """

    def __init__(
        self,
        approver: Optional[Approver] = None,
        guarded_tools: Sequence[str] = DEFAULT_GUARDED_TOOLS,
    ) -> None:
        self._approver: Approver = approver or deny_by_default
        self._guarded = set(guarded_tools)
        self.audit_log: List[Dict[str, Any]] = []

    def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
        """Strands entry point: subscribe to the pre-tool-call lifecycle event."""
        registry.add_callback(BeforeToolCallEvent, self._on_before_tool_call)

    def _on_before_tool_call(self, event: BeforeToolCallEvent) -> None:
        tool_use = event.tool_use or {}
        tool_name = tool_use.get("name", "")

        # Non-dispatch tools (diagnostics, KB lookups, silent ticket logging) run
        # freely — the gate only guards physical mobilization.
        if tool_name not in self._guarded:
            return

        request = ApprovalRequest(
            tool_name=tool_name,
            tool_input=dict(tool_use.get("input") or {}),
            tool_use_id=tool_use.get("toolUseId", ""),
        )
        decision = self._approver(request)
        self._record(request, decision)

        if not decision.approved:
            logger.warning(
                f"Dispatch BLOCKED pending approval: tool={tool_name} reason={decision.reason}"
            )
            # Setting cancel_tool makes Strands skip execution and surface this
            # string as an error-status tool result. No SNS page is sent.
            event.cancel_tool = (
                f"DISPATCH HELD FOR HUMAN APPROVAL. {decision.reason} "
                "No technician has been mobilized. Await operator sign-off before "
                "retrying, or resolve the incident without physical dispatch."
            )
        else:
            logger.info(f"Dispatch APPROVED by {decision.approver}: tool={tool_name}")

    def _record(self, request: ApprovalRequest, decision: ApprovalDecision) -> None:
        self.audit_log.append(
            {
                "tool_name": request.tool_name,
                "tool_use_id": request.tool_use_id,
                "approved": decision.approved,
                "reason": decision.reason,
                "approver": decision.approver,
                "requested_at": request.requested_at.isoformat(),
                "client_name": request.tool_input.get("client_name"),
                "urgency_level": request.tool_input.get("urgency_level"),
            }
        )

    @property
    def held_count(self) -> int:
        """Number of dispatch requests held (denied) so far."""
        return sum(1 for entry in self.audit_log if not entry["approved"])
