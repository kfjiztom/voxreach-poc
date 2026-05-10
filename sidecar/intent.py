"""Intent + entity extraction.

For the POC this is intentionally rule-based: it scans new transcript turns
for menu items by name, infers quantities and modifiers, and updates the
draft order. Production version would call the retrieval LLM with a
structured-output schema, but rules are sufficient for the demo path and
make the behavior deterministic on stage.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from schema import OrderItem, OrderTicket

KNOWLEDGE_PATH = Path(__file__).resolve().parent.parent / "knowledge" / "hearth_and_pass.json"

_NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 9,
    "a": 1, "an": 1,
}

# Word-form time parser — covers the natural way callers say pickup times.
_HOUR_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
}
_MINUTE_WORDS = {
    "oh": 0, "o'clock": 0, "fifteen": 15, "thirty": 30, "forty-five": 45, "forty five": 45,
    "ten": 10, "twenty": 20, "twenty-five": 25, "twenty five": 25, "five": 5,
}

_NAME_TRIGGERS = ("under", "for", "name is", "name's", "this is", "it's", "i'm", "im")
_NAME_BLOCKLIST = {
    "Bulgogi", "Bibimbap", "Mandu", "Pajeon", "Japchae", "Kimchi",
    "Boricha", "Sejak", "Yuja", "Insam", "Galbi", "Hotteok", "Haemul",
    "Hearth", "Pass", "Vox", "Toast", "Korean", "Locust", "Des", "Moines",
}


def _dollars_to_cents(s: str) -> int:
    return int(round(float(s.replace("$", "").strip()) * 100))


def _resolve_price_cents(dish: dict[str, Any]) -> int:
    """Resolve a dish's headline price to integer cents.

    For dishes with multiple price points (lunch/dinner, half/full) we use the
    cheaper as the default for order-build purposes; the Vox persona is expected
    to confirm the size with the caller anyway.
    """
    for key in ("price", "price_lunch", "price_half"):
        if key in dish:
            return _dollars_to_cents(dish[key])
    raise ValueError(f"No price field on dish: {dish.get('name')}")


def _load_menu_index() -> dict[str, dict[str, Any]]:
    """Build a flat lookup of menu items keyed by lowercase name + common aliases."""
    raw = json.loads(KNOWLEDGE_PATH.read_text())
    index: dict[str, dict[str, Any]] = {}
    for section in raw["menu"].values():
        for dish in section:
            name = dish["name"]
            price = _resolve_price_cents(dish)
            entry = {"name": name, "unit_price_cents": price, "raw": dish}
            index[name.lower()] = entry
            # Common alias: strip parentheticals and Korean qualifiers
            simple = re.sub(r"\(.+?\)", "", name).strip().lower()
            if simple and simple != name.lower():
                index[simple] = entry
            # First-word alias (bulgogi, japchae, mandu, etc.)
            first = name.split()[0].lower()
            if len(first) >= 4 and first not in {"kimchi"}:
                index.setdefault(first, entry)
    return index


_MENU_INDEX = _load_menu_index()


def _detect_quantity(text: str, dish_name: str) -> int:
    """Look immediately before the dish name for a quantity word or digit."""
    pattern = re.compile(rf"(\b\d+\b|\b(?:{'|'.join(_NUMBER_WORDS)})\b)\s+(?:\w+\s+){{0,3}}{re.escape(dish_name)}", re.IGNORECASE)
    m = pattern.search(text)
    if not m:
        return 1
    raw = m.group(1).lower()
    if raw.isdigit():
        return max(1, int(raw))
    return _NUMBER_WORDS.get(raw, 1)


def _parse_customer_name(text: str) -> str | None:
    lower = text.lower()
    for trigger in _NAME_TRIGGERS:
        idx = lower.find(trigger)
        if idx == -1:
            continue
        tail = text[idx + len(trigger):]
        m = re.search(r"\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)", tail)
        if m:
            candidate = m.group(1).strip()
            first = candidate.split()[0]
            if first not in _NAME_BLOCKLIST:
                return candidate
    return None


def _parse_pickup_time(text_lower: str) -> str | None:
    # Digit form: "6:30 pm", "6 pm", "12:15"
    m = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)\b", text_lower)
    if m:
        hour, minute, ampm = m.groups()
        return f"{int(hour)}:{minute or '00'} {ampm.replace('.', '').upper()}"

    # Spelled-out form: "six thirty", "seven o'clock", "five forty five"
    hour_pattern = "|".join(_HOUR_WORDS.keys())
    minute_pattern = "|".join(re.escape(k) for k in sorted(_MINUTE_WORDS.keys(), key=len, reverse=True))
    m = re.search(
        rf"\b({hour_pattern})(?:\s+({minute_pattern}))?\b",
        text_lower,
    )
    if m:
        hour_word, minute_word = m.group(1), m.group(2)
        # Don't confuse a quantity ("two pajeon") with a time. Require a time-context word nearby.
        context_window = text_lower[max(0, m.start() - 25): m.end() + 15]
        time_signals = ("pickup", "pick up", "ready", "come in", "arrive", "be there", "stop by", "at ", "by ")
        if not any(sig in context_window for sig in time_signals):
            return None
        hour = _HOUR_WORDS[hour_word]
        minute = _MINUTE_WORDS.get(minute_word, 0) if minute_word else 0
        # Default to PM for ambiguous hours 1–11 (most pickup times are evening)
        ampm = "PM" if 1 <= hour <= 11 else "AM"
        return f"{hour}:{minute:02d} {ampm}"
    return None


def update_order_from_turn(order: OrderTicket, turn_text: str, role: str) -> tuple[OrderTicket, list[str]]:
    """Mutate-and-return the draft order based on a single transcript turn.

    Only customer turns add items. Vox turns can update name/phone/pickup_time
    via lightweight pattern matching (e.g. "under what name?" → next customer
    turn captures the name).

    Returns (order, list of human-readable change descriptions for the SSE log).
    """
    changes: list[str] = []
    text = turn_text.lower()

    if role == "customer":
        # Detect menu items by name match
        for key, entry in _MENU_INDEX.items():
            if key in text:
                qty = _detect_quantity(turn_text, key)
                # Avoid duplicate-line growth on Vox confirmation echoes —
                # if the item is already in the order, we increment quantity
                # only when the customer explicitly says a higher number.
                existing = next((i for i in order.items if i.name == entry["name"]), None)
                if existing is None:
                    item = OrderItem(
                        name=entry["name"],
                        quantity=qty,
                        unit_price_cents=entry["unit_price_cents"],
                        line_total_cents=entry["unit_price_cents"] * qty,
                    )
                    order.items.append(item)
                    changes.append(f"+ {qty}× {entry['name']} ({item.display_price})")
                elif qty > existing.quantity:
                    delta = qty - existing.quantity
                    existing.quantity = qty
                    existing.line_total_cents = existing.unit_price_cents * qty
                    changes.append(f"~ {entry['name']} → {qty}× (+{delta})")

        # Capture pickup time — digit form first, then spelled-out
        if not order.pickup_time:
            pickup = _parse_pickup_time(text)
            if pickup:
                order.pickup_time = pickup
                changes.append(f"pickup_time = {order.pickup_time}")

        # Capture phone (loose 10-digit match)
        m = re.search(r"\b(\d{3})[-.\s]?(\d{3})[-.\s]?(\d{4})\b", turn_text)
        if m and not order.customer_phone:
            order.customer_phone = f"({m.group(1)}) {m.group(2)}-{m.group(3)}"
            changes.append(f"phone = {order.customer_phone}")

        # Capture name. Two-pass: case-insensitive scan for trigger phrases
        # ("under", "for", "name is", "this is", "it's", "i'm"), then look for
        # the next capitalized word in the original-case text.
        if not order.customer_name:
            name = _parse_customer_name(turn_text)
            if name:
                order.customer_name = name
                changes.append(f"name = {order.customer_name}")

    return order, changes
