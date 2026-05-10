"""In-memory call state and SSE event broker.

Single-process, single-call POC — we keep one CallState in module memory and
push every mutation to a fan-out asyncio.Queue per connected SSE client.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime

from schema import CallState, OrderTicket, SSEEvent


class CallStore:
    def __init__(self) -> None:
        self._state: CallState | None = None
        self._subscribers: list[asyncio.Queue[SSEEvent]] = []
        self._lock = asyncio.Lock()

    @property
    def current(self) -> CallState | None:
        return self._state

    async def start_call(self) -> CallState:
        async with self._lock:
            call_id = uuid.uuid4().hex[:8]
            self._state = CallState(
                call_id=call_id,
                status="connected",
                started_at=datetime.utcnow(),
                order=OrderTicket(call_id=call_id),
            )
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

    async def publish(self, event: SSEEvent) -> None:
        async with self._lock:
            await self._publish(event)

    async def _publish(self, event: SSEEvent) -> None:
        # Caller already holds the lock when going through start_call/end_call;
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
