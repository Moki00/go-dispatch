"""
Go-Dispatch: Autonomous SLA Countdown Daemon

A proactive scheduler that tracks the SLA deadline of every active ticket and
raises a Tier-3 warning the moment a ticket crosses its breach threshold
(default: <=25% of the contract window remaining) — *without* waiting for a new
inbound alert. This is the autonomous counterpart to the reactive webhook path:
tickets that go quiet still get escalated before they breach.

Design notes:
- The countdown math is pure and driven by an **injectable clock**, so warning
  behavior is fully deterministic and testable without real time passing.
- :meth:`SLAMonitor.check` is idempotent — a ticket is warned at most once until
  it is resolved — so it is safe to poll on a tight interval.
- :meth:`SLAMonitor.run` is a thin async loop that periodically calls ``check``
  and can be cancelled cleanly via a ``stop_event``; it is started from the
  FastAPI application lifespan.
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional

logger = logging.getLogger("go_dispatch.scheduler")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class TrackedTicket:
    """An active ticket whose SLA deadline the daemon is counting down."""

    ticket_id: str
    client_name: str
    sla_window_minutes: int
    started_at: datetime
    site_address: str = ""
    issue_summary: str = ""
    warned: bool = False

    def deadline(self) -> datetime:
        return self.started_at + timedelta(minutes=self.sla_window_minutes)

    def remaining_minutes(self, now: datetime) -> float:
        return (self.deadline() - now).total_seconds() / 60.0

    def remaining_fraction(self, now: datetime) -> float:
        """Portion of the SLA window still remaining (1.0 at start, <=0 at breach)."""
        if self.sla_window_minutes <= 0:
            return 0.0
        return self.remaining_minutes(now) / self.sla_window_minutes


# Invoked once per ticket the moment it crosses the warning threshold.
WarningCallback = Callable[[TrackedTicket, float], None]


class SLAMonitor:
    """Proactive SLA countdown tracker with a pluggable warning callback."""

    def __init__(
        self,
        warn_threshold: float = 0.25,
        on_warning: Optional[WarningCallback] = None,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._warn_threshold = warn_threshold
        self._on_warning = on_warning
        self._clock = clock
        self._tickets: Dict[str, TrackedTicket] = {}

    # ------------------------------------------------------------------ #
    # Registration
    # ------------------------------------------------------------------ #
    def register(
        self,
        ticket_id: str,
        client_name: str,
        sla_window_minutes: int,
        *,
        started_at: Optional[datetime] = None,
        site_address: str = "",
        issue_summary: str = "",
    ) -> TrackedTicket:
        """Begin (or restart) the SLA countdown for a ticket."""
        ticket = TrackedTicket(
            ticket_id=ticket_id,
            client_name=client_name,
            sla_window_minutes=sla_window_minutes,
            started_at=started_at or self._clock(),
            site_address=site_address,
            issue_summary=issue_summary,
        )
        self._tickets[ticket_id] = ticket
        logger.info(
            f"SLA monitor tracking {ticket_id} "
            f"(window {sla_window_minutes}m, deadline {ticket.deadline().isoformat()})"
        )
        return ticket

    def resolve(self, ticket_id: str) -> None:
        """Stop tracking a ticket (resolved / dispatched / cancelled)."""
        if self._tickets.pop(ticket_id, None) is not None:
            logger.info(f"SLA monitor released {ticket_id}")

    # Semantic alias: removing a ticket from the countdown.
    deregister = resolve

    @property
    def active_ticket_ids(self) -> List[str]:
        return list(self._tickets)

    def remaining_minutes(self, ticket_id: str) -> Optional[float]:
        ticket = self._tickets.get(ticket_id)
        return None if ticket is None else ticket.remaining_minutes(self._clock())

    # ------------------------------------------------------------------ #
    # The countdown check
    # ------------------------------------------------------------------ #
    def check(self) -> List[TrackedTicket]:
        """Evaluate every tracked ticket; warn (once) on threshold crossings.

        Returns the tickets newly warned on this pass. Idempotent: a ticket is
        warned at most once until it is resolved and re-registered.
        """
        now = self._clock()
        newly_warned: List[TrackedTicket] = []

        for ticket in self._tickets.values():
            if ticket.warned:
                continue
            fraction = ticket.remaining_fraction(now)
            if fraction <= self._warn_threshold:
                ticket.warned = True
                newly_warned.append(ticket)
                logger.warning(
                    f"SLA WARNING {ticket.ticket_id}: "
                    f"{ticket.remaining_minutes(now):.1f}m ({fraction * 100:.0f}%) remaining "
                    f"<= threshold {self._warn_threshold * 100:.0f}%"
                )
                if self._on_warning is not None:
                    try:
                        self._on_warning(ticket, fraction)
                    except Exception as exc:  # noqa: BLE001 - a bad callback must not kill the daemon
                        logger.error(
                            f"SLA warning callback failed for {ticket.ticket_id}: {exc}"
                        )

        return newly_warned

    # ------------------------------------------------------------------ #
    # Async daemon loop
    # ------------------------------------------------------------------ #
    async def run(
        self,
        interval_seconds: float = 30.0,
        stop_event: Optional[asyncio.Event] = None,
    ) -> None:
        """Run the countdown loop until ``stop_event`` is set (or forever).

        Checks immediately on entry, then every ``interval_seconds``. When a
        ``stop_event`` is supplied the loop wakes early on stop for a clean,
        prompt shutdown.
        """
        logger.info(f"Starting SLA countdown daemon (interval {interval_seconds}s)")
        try:
            while stop_event is None or not stop_event.is_set():
                self.check()
                if stop_event is not None:
                    try:
                        await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
                    except asyncio.TimeoutError:
                        continue
                else:
                    await asyncio.sleep(interval_seconds)
        except asyncio.CancelledError:
            logger.info("SLA countdown daemon cancelled")
            raise
        logger.info("SLA countdown daemon stopped")
