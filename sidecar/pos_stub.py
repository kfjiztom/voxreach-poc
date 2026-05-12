"""Fake POS write — the demo's moat moment.

In production this would be the per-tenant POS adapter (Toast / Square / Clover).
For the POC it logs a structured JSON payload that mirrors what a Toast Order
API call would look like, and returns success after a short artificial delay
so the UI can animate the 'writing → written' transition.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path

from schema import OrderItem, OrderTicket

LOG_DIR = Path(__file__).resolve().parent.parent / ".pos-writes"
log = logging.getLogger("voxreach.pos_stub")


def _toast_modifiers(item: OrderItem) -> list[dict]:
    """Flatten an OrderItem's customization fields into a Toast-style modifier list.

    Spice level is treated as a special modifier so it appears on the kitchen
    ticket alongside menu-defined options (protein swap, portion).
    """
    mods: list[dict] = []
    if item.modifier:
        mods.append({"name": item.modifier})
    if item.spice_level:
        mods.append({"name": f"spice: {item.spice_level}"})
    return mods


def _toast_payload(order: OrderTicket) -> dict:
    """Shape the order roughly like a Toast Orders API v2 request body."""
    return {
        "diningOption": {"guid": "TAKEOUT"},
        "guestName": order.customer_name or "Phone Guest",
        "guestPhone": order.customer_phone,
        "promisedDate": order.pickup_time,
        # Only ACTIVE items go to the POS — removed items stay in the conversation
        # transcript for debugging but never become real kitchen tickets.
        "selections": [
            {
                "displayName": item.name,
                "quantity": item.quantity,
                "unitPrice": item.unit_price_cents / 100,
                "lineTotal": item.line_total_cents / 100,
                "modifiers": _toast_modifiers(item),
                "specialRequest": item.notes,
            }
            for item in order.active_items
        ],
        "subtotal": order.subtotal_cents / 100,
        "source": {"name": "VoxReach", "callId": order.call_id},
        "submittedAt": datetime.utcnow().isoformat() + "Z",
    }


async def write_order(order: OrderTicket) -> dict:
    """Pretend to write to Toast. Returns the response payload."""
    payload = _toast_payload(order)
    await asyncio.sleep(0.6)  # simulate network round-trip — visible in the UI

    LOG_DIR.mkdir(exist_ok=True)
    log_file = LOG_DIR / f"{order.call_id}.json"
    log_file.write_text(json.dumps(payload, indent=2))

    response = {
        "guid": f"toast-stub-{order.call_id}",
        "orderNumber": f"H{order.call_id[-4:].upper()}",
        "status": "FIRED_TO_KITCHEN",
        "kitchenDisplay": "KDS-Line-1",
        "estimatedReadyAt": order.pickup_time,
    }
    log.info("pos_write call_id=%s order=%s", order.call_id, response["orderNumber"])
    return response
