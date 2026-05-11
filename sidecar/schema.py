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


# ---------------------------------------------------------------------------
# Order — three states an item can be in during a conversation
# ---------------------------------------------------------------------------

ItemStatus = Literal["pending", "confirmed", "removed"]


class OrderItem(BaseModel):
    name: str
    quantity: int = 1
    unit_price_cents: int
    modifier: str | None = None
    line_total_cents: int
    status: ItemStatus = "pending"  # set to "confirmed" once Vox reads it back

    @property
    def display_price(self) -> str:
        return f"${self.line_total_cents / 100:.2f}"


class OrderTicket(BaseModel):
    """The structured order that would be written to the POS.

    Items list includes ALL items ever mentioned (including removed ones, with
    status='removed'). The web UI strikes through removed items for the demo
    moment. The subtotal property counts only non-removed items so the
    POS-write JSON is correct.
    """

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
    def active_items(self) -> list[OrderItem]:
        return [i for i in self.items if i.status != "removed"]

    @property
    def subtotal_cents(self) -> int:
        return sum(item.line_total_cents for item in self.active_items)

    @property
    def display_subtotal(self) -> str:
        return f"${self.subtotal_cents / 100:.2f}"


# ---------------------------------------------------------------------------
# Order extraction — what the LLM returns each turn
# ---------------------------------------------------------------------------


class ExtractedItem(BaseModel):
    """One item as currently understood from the rolling transcript.

    The LLM returns the COMPLETE current state every turn, not deltas.
    The sidecar diffs against the previous state to emit granular events.
    """

    name: str  # must match a menu item name exactly
    quantity: int = 1
    modifier: str | None = None
    confirmed: bool = False  # true once Vox has read this item back to the caller


class OrderExtractionResult(BaseModel):
    """Full extraction result from the LLM for one turn."""

    items: list[ExtractedItem] = Field(default_factory=list)
    customer_name: str | None = None
    customer_phone: str | None = None
    pickup_time: str | None = None
    notes: str | None = None
    caller_finished: bool = False  # true if the caller has indicated they're done ordering


class OrderDiff(BaseModel):
    """The delta computed by diffing two consecutive extraction results.

    Each diff produces zero-or-more granular events that the UI animates:
      - added: new item appeared
      - removed: item from previous state is gone (customer cancelled)
      - quantity_changed: same item, different qty
      - modifier_changed: same item, different modifier
      - confirmed: item flipped from pending → confirmed
    """

    added: list[ExtractedItem] = Field(default_factory=list)
    removed: list[ExtractedItem] = Field(default_factory=list)
    quantity_changed: list[tuple[str, int, int]] = Field(default_factory=list)  # (name, old, new)
    modifier_changed: list[tuple[str, str | None, str | None]] = Field(default_factory=list)
    confirmed: list[str] = Field(default_factory=list)  # item names newly confirmed

    @property
    def is_empty(self) -> bool:
        return not any([
            self.added, self.removed, self.quantity_changed,
            self.modifier_changed, self.confirmed,
        ])


# ---------------------------------------------------------------------------
# Retrieval + latency (mostly unchanged)
# ---------------------------------------------------------------------------


class RetrievalHit(BaseModel):
    """One knowledge-base item used to ground a response."""

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
    extraction_latency_ms: int | None = None  # how long the LLM extractor took


# ---------------------------------------------------------------------------
# Call state + SSE event envelope
# ---------------------------------------------------------------------------


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
        "item_added",
        "item_removed",
        "item_modified",
        "item_confirmed",
        "retrieval_hit",
        "latency_updated",
        "pos_write",
    ]
    data: dict
