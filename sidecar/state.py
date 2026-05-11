"""In-memory call state and SSE event broker.

Single-process, single-call POC — we keep one CallState in module memory and
push every mutation to a fan-out asyncio.Queue per connected SSE client.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime

from order_extractor import canonicalize, diff_extractions
from schema import (
    CallState,
    OrderExtractionResult,
    OrderItem,
    OrderTicket,
    SSEEvent,
)

log = logging.getLogger("voxreach.state")


class CallStore:
    def __init__(self) -> None:
        self._state: CallState | None = None
        self._last_extraction: OrderExtractionResult | None = None
        self._subscribers: list[asyncio.Queue[SSEEvent]] = []
        self._lock = asyncio.Lock()

    @property
    def current(self) -> CallState | None:
        return self._state

    @property
    def last_extraction(self) -> OrderExtractionResult | None:
        return self._last_extraction

    async def start_call(self) -> CallState:
        async with self._lock:
            call_id = uuid.uuid4().hex[:8]
            self._state = CallState(
                call_id=call_id,
                status="connected",
                started_at=datetime.utcnow(),
                order=OrderTicket(call_id=call_id),
            )
            self._last_extraction = None
            await self._publish(SSEEvent(event="call_started", data=self._state.model_dump(mode="json")))
            return self._state

    async def end_call(self) -> CallState | None:
        async with self._lock:
            if self._state is None:
                return None
            self._state.status = "ended"
            self._state.ended_at = datetime.utcnow()
            await self._publish(SSEEvent(event="call_ended", data=self._state.model_dump(mode="json")))
            return self._state

    async def reset(self) -> None:
        async with self._lock:
            self._state = None
            self._last_extraction = None

    async def apply_extraction(self, extraction: OrderExtractionResult) -> None:
        """Update the order based on a new LLM/rule extraction result.

        Diffs against the previous extraction to emit granular events. Mutates
        the call's order to reflect the new state — including marking previously-
        present items as 'removed' (not deleting them) so the UI can show
        strikethrough.
        """
        async with self._lock:
            state = self._state
            if state is None:
                return

            diff = diff_extractions(self._last_extraction, extraction)

            # Update non-item fields
            if extraction.customer_name and state.order.customer_name != extraction.customer_name:
                state.order.customer_name = extraction.customer_name
            if extraction.customer_phone and state.order.customer_phone != extraction.customer_phone:
                state.order.customer_phone = extraction.customer_phone
            if extraction.pickup_time and state.order.pickup_time != extraction.pickup_time:
                state.order.pickup_time = extraction.pickup_time
            if extraction.notes and state.order.notes != extraction.notes:
                state.order.notes = extraction.notes

            # Apply item-level changes
            curr_by_name = {i.name: i for i in extraction.items}

            # Mark removed items
            for removed in diff.removed:
                for item in state.order.items:
                    if item.name == removed.name and item.status != "removed":
                        item.status = "removed"
                        await self._publish(SSEEvent(
                            event="item_removed",
                            data={"name": removed.name},
                        ))

            # Add new items
            for added in diff.added:
                entry = canonicalize(added.name)
                if entry is None:
                    log.warning("extraction returned unknown item %r; skipping add", added.name)
                    continue
                unit = entry["unit_price_cents"]
                new_item = OrderItem(
                    name=entry["name"],
                    quantity=added.quantity,
                    unit_price_cents=unit,
                    modifier=added.modifier,
                    line_total_cents=unit * added.quantity,
                    status="confirmed" if added.confirmed else "pending",
                )
                state.order.items.append(new_item)
                await self._publish(SSEEvent(
                    event="item_added",
                    data=new_item.model_dump(mode="json"),
                ))

            # Quantity changes
            for name, old_qty, new_qty in diff.quantity_changed:
                for item in state.order.items:
                    if item.name == name and item.status != "removed":
                        item.quantity = new_qty
                        item.line_total_cents = item.unit_price_cents * new_qty
                        await self._publish(SSEEvent(
                            event="item_modified",
                            data={"name": name, "field": "quantity", "old": old_qty, "new": new_qty},
                        ))

            # Modifier changes
            for name, old_mod, new_mod in diff.modifier_changed:
                for item in state.order.items:
                    if item.name == name and item.status != "removed":
                        item.modifier = new_mod
                        await self._publish(SSEEvent(
                            event="item_modified",
                            data={"name": name, "field": "modifier", "old": old_mod, "new": new_mod},
                        ))

            # Confirmation flips (Vox echoed the item back)
            for name in diff.confirmed:
                for item in state.order.items:
                    if item.name == name and item.status == "pending":
                        item.status = "confirmed"
                        await self._publish(SSEEvent(
                            event="item_confirmed",
                            data={"name": name},
                        ))

            # Sync confirmed flag from extraction onto items
            for name, ex_item in curr_by_name.items():
                if ex_item.confirmed:
                    for item in state.order.items:
                        if item.name == name and item.status == "pending":
                            item.status = "confirmed"

            # Always emit an order_updated snapshot so UIs that don't track
            # granular events can still render correctly.
            if not diff.is_empty:
                await self._publish(SSEEvent(
                    event="order_updated",
                    data={"order": state.order.model_dump(mode="json")},
                ))

            self._last_extraction = extraction

    async def publish(self, event: SSEEvent) -> None:
        async with self._lock:
            await self._publish(event)

    async def _publish(self, event: SSEEvent) -> None:
        # Caller already holds the lock when going through start_call/end_call/apply_extraction;
        # publish() acquires it explicitly for outside callers.
        for q in list(self._subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass

    def subscribe(self) -> asyncio.Queue[SSEEvent]:
        q: asyncio.Queue[SSEEvent] = asyncio.Queue(maxsize=256)
        self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[SSEEvent]) -> None:
        if q in self._subscribers:
            self._subscribers.remove(q)


store = CallStore()
