from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class TranscriptTurn(BaseModel):
    """One side of one turn in the conversation."""

    role: Literal["customer", "vox"]
    text: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    latency_ms: int | None = None


class OrderItem(BaseModel):
    name: str
    quantity: int = 1
    unit_price_cents: int
    modifier: str | None = None
    line_total_cents: int

    @property
    def display_price(self) -> str:
        return f"${self.line_total_cents / 100:.2f}"


class OrderTicket(BaseModel):
    """The structured order that would be written to the POS."""

    call_id: str
    restaurant: str = "Hearth & Pass"
    customer_name: str | None = None
    customer_phone: str | None = None
    items: list[OrderItem] = Field(default_factory=list)
    pickup_time: str | None = None
    notes: str | None = None
    status: Literal["draft", "confirmed", "written_to_pos"] = "draft"
    created_at: datetime = Field(default_factory=datetime.utcnow)

    @property
    def subtotal_cents(self) -> int:
        return sum(item.line_total_cents for item in self.items)

    @property
    def display_subtotal(self) -> str:
        return f"${self.subtotal_cents / 100:.2f}"


class RetrievalHit(BaseModel):
    """One knowledge-base item the RAG layer pulled in to ground a response."""

    path: str  # e.g. "menu.mains.bulgogi"
    snippet: str
    score: float | None = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class LatencyMetric(BaseModel):
    first_audio_ms: int | None = None
    last_turn_ms: int | None = None
    avg_turn_ms: int | None = None
    rag_hits: int = 0
    asr_confidence: float | None = None


class CallState(BaseModel):
    call_id: str
    status: Literal["idle", "ringing", "connected", "ended"] = "idle"
    started_at: datetime | None = None
    ended_at: datetime | None = None
    transcript: list[TranscriptTurn] = Field(default_factory=list)
    order: OrderTicket
    retrieval_log: list[RetrievalHit] = Field(default_factory=list)
    latency: LatencyMetric = Field(default_factory=LatencyMetric)
    pos_write_status: Literal["pending", "writing", "written", "failed"] = "pending"


class SSEEvent(BaseModel):
    """Envelope for events streamed to the frontend."""

    event: Literal[
        "call_started",
        "call_ended",
        "transcript_turn",
        "order_updated",
        "retrieval_hit",
        "latency_updated",
        "pos_write",
    ]
    data: dict
