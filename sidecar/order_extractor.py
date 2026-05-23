"""Order extraction — pluggable, with two backends.

The default `LLMExtractor` calls a local Ollama-served Gemma-2-9B (or similar)
to convert a rolling transcript into the complete current order state. Because
the LLM sees the full conversation context each turn, it handles cancels and
modifications naturally — the sidecar just diffs the new state vs the previous
state to emit granular UI events.

`RuleBasedExtractor` is the legacy regex-based extractor we built first. It's
fragile (only appends, can't handle cancel/modify) but useful as a fallback
when Ollama is unavailable, and for fast unit tests.

The factory `get_extractor()` picks based on `VOXREACH_EXTRACTOR` env var.
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Protocol

import httpx

from schema import ExtractedItem, OrderExtractionResult, TranscriptTurn

log = logging.getLogger("voxreach.extractor")

KNOWLEDGE_PATH = Path(__file__).resolve().parent.parent / "knowledge" / "hearth_and_pass.json"


# ---------------------------------------------------------------------------
# Menu index — shared by both extractors so item names are canonicalized
# ---------------------------------------------------------------------------


def _load_menu_index() -> dict[str, dict[str, Any]]:
    """Build a flat lookup of menu items keyed by lowercase name + aliases.

    Returns: {alias: {"name": canonical_name, "unit_price_cents": int, ...}}
    """
    raw = json.loads(KNOWLEDGE_PATH.read_text())
    index: dict[str, dict[str, Any]] = {}
    for section in raw["menu"].values():
        for dish in section:
            name = dish["name"]
            price = _resolve_price_cents(dish)
            entry = {"name": name, "unit_price_cents": price, "raw": dish}
            index[name.lower()] = entry
            # Stripped parenthetical alias — "Insam (Ginseng) Tea" → "insam tea"
            simple = re.sub(r"\(.+?\)", "", name).strip().lower()
            simple = re.sub(r"\s+", " ", simple)  # collapse double spaces from the strip
            if simple and simple != name.lower():
                index[simple] = entry
            # First-word alias — "Bulgogi", "Japchae", "Mandu", "Bibimbap"
            first = name.split()[0].lower()
            if len(first) >= 4 and first not in {"kimchi"}:
                index.setdefault(first, entry)
            # Parenthetical-content alias — for items where the customer-facing
            # word lives INSIDE the parenthetical:
            #   "Insam (Ginseng) Tea"  → caller says "ginseng tea" not "insam"
            #   "Mandu (Beef & Chive)" → caller says "beef" or "chive"
            #   "Bibimbap (Stone Bowl)" → caller might say "stone bowl"
            # Pull each word inside the parens; if there's a noun after the
            # paren (like "Tea"), also register the "<paren-word> <suffix>"
            # form so "ginseng tea" lookups land directly on this entry.
            paren_match = re.search(r"\((.+?)\)", name)
            if paren_match:
                paren_text = paren_match.group(1).strip().lower()
                paren_words = [w for w in re.split(r"[\s&]+", paren_text) if len(w) >= 4]
                suffix_match = re.search(r"\)\s*(.+)$", name)
                suffix = suffix_match.group(1).strip().lower() if suffix_match else ""
                for word in paren_words:
                    index.setdefault(word, entry)
                    if suffix:
                        index.setdefault(f"{word} {suffix}", entry)
    return index


def _resolve_price_cents(dish: dict[str, Any]) -> int:
    for key in ("price", "price_lunch", "price_half"):
        if key in dish:
            return int(round(float(dish[key].replace("$", "").strip()) * 100))
    raise ValueError(f"No price field on dish: {dish.get('name')}")


MENU_INDEX = _load_menu_index()


def canonicalize(name: str) -> dict[str, Any] | None:
    """Map a fuzzy item name to the canonical menu entry, or None if no match."""
    key = name.lower().strip()
    if key in MENU_INDEX:
        return MENU_INDEX[key]
    # Try first word match
    first = key.split()[0] if key else ""
    if first and first in MENU_INDEX:
        return MENU_INDEX[first]
    return None


# ---------------------------------------------------------------------------
# Defensive JSON extraction — Gemma occasionally wraps output in markdown
# fences or emits trailing prose. We pull the first balanced {...} block.
# ---------------------------------------------------------------------------


def _clean_str(v: Any) -> str | None:
    """Coerce LLM-emitted string-or-null to a stripped string or None.

    Gemma sometimes returns the literal string "null" or empty strings — treat
    both as None so downstream diff logic doesn't see spurious changes.
    """
    if v is None:
        return None
    if not isinstance(v, str):
        v = str(v)
    s = v.strip()
    if not s or s.lower() in {"null", "none", "n/a", "na"}:
        return None
    return s


def _extract_json_object(text: str) -> dict | None:
    """Return the first balanced JSON object in `text`, or None if none."""
    if not text:
        return None
    s = text.strip()
    # Strip ```json ... ``` or ``` ... ``` fences
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*", "", s)
        s = re.sub(r"\s*```$", "", s)
    start = s.find("{")
    if start == -1:
        return None
    # Walk braces, respecting strings to avoid counting braces inside JSON strings
    depth = 0
    in_str = False
    escape = False
    for i in range(start, len(s)):
        c = s[i]
        if escape:
            escape = False
            continue
        if c == "\\":
            escape = True
            continue
        if c == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(s[start:i + 1])
                except json.JSONDecodeError:
                    return None
    return None


# ---------------------------------------------------------------------------
# Extractor interface
# ---------------------------------------------------------------------------


class Extractor(Protocol):
    """Common interface so callers don't care which backend is in use."""

    def extract(self, transcript: list[TranscriptTurn]) -> OrderExtractionResult:
        """Given the full rolling transcript, return the current order state."""
        ...


# ---------------------------------------------------------------------------
# LLM-based extractor (Ollama + Gemma)
# ---------------------------------------------------------------------------


SYSTEM_PROMPT = """You extract restaurant takeout orders from a phone-call transcript between a customer and Vox (the AI host at Hearth & Pass).

You will receive the FULL conversation so far. Return the CURRENT state of the customer's ORDER — what the CUSTOMER actually asked for, after any cancellations, swaps, or quantity changes.

DEFAULT TO EMPTY. If the transcript has no clear customer order, return items=[].
A short conversation with no clear order is the most common case at call start —
do not invent items to fill the order. NEVER include an item unless you can quote
a specific customer utterance that asked for it.

CRITICAL — what counts as an "ordered" item:
An item is ordered ONLY if the CUSTOMER explicitly asked for it. Look for customer phrases like:
  - "I'd like a {item}"
  - "Can I get {item}"
  - "Give me {item}"
  - "I'll have {item}"
  - "Add a {item}"
  - "Make it {n} {item}s"
  - "Yes" in direct response to Vox's specific suggestion ("would you like a tea?" → "Yes")

DO NOT EXTRACT items that appear only in:
  - Vox listing the menu ("we have bulgogi, japchae, haemul pajeon, kimchi trio, ...") — these are NOT orders
  - Vox suggesting something the customer didn't accept ("would you like to try the japchae?" with no customer yes)
  - Vox's GREETING — Vox often mentions the most popular item in greeting; that is NOT an order
  - Customer asking a QUESTION about an item — these are NEVER orders:
      * "What's in the bibimbap?"
      * "Is the kimchi jjigae spicy?"
      * "Can you tell me about the japchae?"
      * "How much is the galbi-jjim?"
      * "Do you have pajeon?"
      * "What sides come with the mandu?"
    A line ending in "?" that mentions an item is asking ABOUT the item, not ordering it.
  - Items mentioned in passing as examples or context
  - Items the customer EXPLICITLY cancelled — "scratch that", "cancel the pajeon",
    "actually no, drop that" should REMOVE the item from your output, not include it.

The rule: if you can't point at a specific CUSTOMER statement (not question) that asked
for this item, do NOT include it. When in doubt, OMIT.

ANTI-HALLUCINATION CHECK: Before you output each item, find the customer line in
the transcript that asked for it. If you cannot, REMOVE that item. The smaller
extractor models tend to over-include the most popular menu item even when the
customer never mentioned it — guard against this explicitly.

CONSISTENCY RULE — once an item has been confirmed by Vox, KEEP IT in your output until
the customer explicitly cancels it. Do not drop a confirmed item just because the
customer started asking unrelated questions or changed topics. The transcript grows;
each extraction must include EVERY item the customer has ordered AND not cancelled
up to this point.

OTHER RULES:
1. Use canonical menu names exactly as listed below. If the customer says a variant ("the rib-eye"), map it to the canonical name ("Bulgogi").
2. If a previous extraction included an item but the customer later cancelled it ("scratch the bulgogi"), do NOT include it now.
3. If the customer changed their mind on quantity ("actually, make it two"), reflect the NEW quantity.
4. Each item has THREE customization fields — keep them in their own buckets:
   - "modifier": the menu-defined option for that dish, when relevant. Use the
     canonical option text from the per-dish list below. Examples:
       Japchae → "tofu", "no protein"  (default beef is implied — leave null)
       Bibimbap → "add bulgogi", "add tofu"
       Galbi-jjim → "half", "full"
     If the customer doesn't pick a menu option, leave modifier=null.
   - "spice_level": ONLY if the customer asked to change spice. Capture verbatim
     and normalize to one of: "mild", "medium", "spicy", "extra spicy", "no spice".
     Examples: "make it mild" → "mild"; "extra spicy please" → "extra spicy";
     "can you make it not spicy" → "no spice". If no spice request, leave null.
   - "notes": free-form per-dish customizations or special requests. Capture
     verbatim, but trim filler. Examples:
       "no onions" → "no onions"
       "sauce on the side" → "sauce on the side"
       "extra crispy" → "extra crispy"
       "split into two boxes" → "split into two boxes"
       "for my daughter, she's allergic to peanuts" → "peanut allergy"
     If no customization, leave null. Order-level pickup/customer notes go in
     the top-level "notes" field, NOT per-item.
5. "confirmed": true ONLY if Vox has READ THE ITEM BACK in a confirmation phrasing ("Let me confirm: one bulgogi…", "got it: bulgogi and pajeon"). Vox merely acknowledging ("of course", "sure") doesn't count.
6. customer_name: extract ONLY when the CUSTOMER themselves stated a name to use
   for the order, or directly confirmed a name Vox repeated back:
     - Customer self-identifying: "it's under Maya", "I'm Sam", "the name is Alex",
       "put it under John", "for Jamie"
     - Customer confirming Vox's read-back: Vox says "got it, Sam — see you at six",
       customer says "yes" or "yep" → name is "Sam"
   Pick the name as the customer SPELLED OR PRONOUNCED IT. Use Title Case.
   If Vox guessed a name and the customer did NOT confirm, leave null.
   If the customer's audio was unclear (STT may produce "Jake" vs "Jacob" vs "Drake")
   prefer the name VOX read back AND the customer accepted, since both sides heard it.
   NEVER invent a name. If the customer never stated one or never confirmed one, return null.
7. customer_phone: extract a 10-digit US phone number from EITHER:
     - The customer's line: "515-555-0182", "five one five five five five zero one eight two"
     - Vox echoing it back AND the customer confirming the echo
   Accept word forms: "five-one-five" = 515, "oh" = 0, "double-three" = 33, "triple-seven" = 777.
   Normalize the OUTPUT to "(NNN) NNN-NNNN" format. The number must have exactly 10 digits;
   if you can only collect fewer (caller never finished spelling it), return null instead of
   guessing the missing digits. Do NOT confuse pickup-time digits (e.g. "six thirty") with
   phone digits — phone numbers come in long sequences of digits, times are 1–2 digit numbers
   followed by AM/PM or "o'clock".
8. pickup_time: format as "H:MM AM/PM" when the customer specifies a time. Accept word forms ("six thirty PM" → "6:30 PM").
9. caller_finished: true only if the customer has explicitly indicated they are done — "that's it", "that's all", "thanks bye", "perfect that's all". Acknowledgements like "okay" alone are NOT finished signals.

CANONICAL MENU NAMES (with available modifiers — only use these options):
- Sejak Green Tea            [no modifier]
- Boricha                    [no modifier]
- Yuja Honey Tea             [no modifier]
- Insam (Ginseng) Tea        [no modifier]
- Kimchi Trio                [no modifier; spice already medium]
- Haemul Pajeon              [no modifier; default spice mild]
- Mandu (Beef & Chive)       [no modifier]
- Japchae                    modifier: "tofu" | "no protein" | null (null = default beef)
- Bibimbap (Stone Bowl)      modifier: "add bulgogi" | "add tofu" | null
- Bulgogi                    [no modifier; default spice mild]
- Kimchi Jjigae              [no modifier; default spice hot]
- Galbi-jjim                 modifier: "half" | "full" (REQUIRED if customer chose)
- Pine-Nut Hotteok           [no modifier]

Return ONLY this JSON object — no prose, no explanation:
{
  "items": [
    {
      "name": "<canonical>",
      "quantity": <int>,
      "modifier": "<menu option or null>",
      "spice_level": "<mild|medium|spicy|extra spicy|no spice or null>",
      "notes": "<free-form per-item customization or null>",
      "confirmed": <bool>
    }
  ],
  "customer_name": "<string or null>",
  "customer_phone": "<string or null>",
  "pickup_time": "<string or null>",
  "notes": "<order-level note, e.g. 'allergic to peanuts' or null>",
  "caller_finished": <bool>
}"""


class LLMExtractor:
    """Calls a local Ollama instance running Gemma (or similar OpenAI-compatible model)."""

    def __init__(
        self,
        ollama_url: str | None = None,
        model: str | None = None,
        timeout: float | None = None,
    ):
        self.ollama_url = ollama_url or os.environ.get("OLLAMA_URL", "http://localhost:11434/v1")
        self.model = model or os.environ.get("VOXREACH_ORDER_MODEL", "gemma2:9b")
        # Bumped to 25s because gemma2:9b on an A40 with the full system prompt
        # and a ~30-turn transcript regularly takes 12-20s. Throttled to once
        # per 3s elsewhere, so a long extraction can't snowball.
        self.timeout = timeout or float(os.environ.get("VOXREACH_EXTRACT_TIMEOUT", "25.0"))
        # Only send the last N turns to the LLM. The earlier conversation is
        # already represented in the previous extraction state we diff against.
        self.max_turns = int(os.environ.get("VOXREACH_EXTRACT_MAX_TURNS", "40"))

    def extract(self, transcript: list[TranscriptTurn]) -> OrderExtractionResult:
        if not transcript:
            return OrderExtractionResult()

        # Cap transcript to most recent turns — keeps token count bounded and
        # latency predictable on long calls. Earlier turns are still reflected
        # in the previous extraction we diff against, so confirmed items
        # remain in state even though the LLM no longer sees the original ask.
        recent = transcript[-self.max_turns:] if len(transcript) > self.max_turns else transcript

        rendered = "\n".join(
            f"{'CUSTOMER' if t.role == 'customer' else 'VOX'}: {t.text}"
            for t in recent
        )

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"Conversation so far:\n\n{rendered}\n\nExtract the current order state as JSON."},
            ],
            "stream": False,
            # OpenAI-compat JSON mode (this endpoint ignores Ollama's native `format`).
            # Recent Ollama supports response_format; older versions ignore it but
            # we still parse defensively below so it doesn't matter.
            "response_format": {"type": "json_object"},
            "temperature": 0.0,
            "max_tokens": 1024,
        }

        try:
            with httpx.Client(timeout=self.timeout) as client:
                r = client.post(f"{self.ollama_url}/chat/completions", json=payload)
                if r.status_code >= 400:
                    # Dump body so we can see what Ollama is upset about
                    log.warning(
                        "LLM extraction HTTP %s: %s", r.status_code, r.text[:400],
                    )
                    return OrderExtractionResult()
                content = r.json()["choices"][0]["message"]["content"] or ""
                parsed = _extract_json_object(content)
                if parsed is None:
                    log.warning("LLM returned no parseable JSON: %r", content[:200])
                    return OrderExtractionResult()
                return self._validate_and_canonicalize(parsed)
        except (httpx.HTTPError, KeyError) as e:
            log.warning("LLM extraction failed (%s); returning empty extraction", e)
            return OrderExtractionResult()

    def _validate_and_canonicalize(self, raw: dict) -> OrderExtractionResult:
        """Drop any item not in our menu index. Map fuzzy names → canonical names."""
        items_out: list[ExtractedItem] = []
        for item in raw.get("items", []) or []:
            entry = canonicalize(item.get("name", ""))
            if entry is None:
                log.warning("LLM returned unknown item %r; dropping", item.get("name"))
                continue
            qty = max(1, int(item.get("quantity", 1) or 1))
            items_out.append(ExtractedItem(
                name=entry["name"],
                quantity=qty,
                modifier=_clean_str(item.get("modifier")),
                spice_level=_clean_str(item.get("spice_level")),
                notes=_clean_str(item.get("notes")),
                confirmed=bool(item.get("confirmed", False)),
            ))

        return OrderExtractionResult(
            items=items_out,
            customer_name=raw.get("customer_name"),
            customer_phone=raw.get("customer_phone"),
            pickup_time=raw.get("pickup_time"),
            notes=raw.get("notes"),
            caller_finished=bool(raw.get("caller_finished", False)),
        )

    def health_check(self) -> bool:
        """Return True if Ollama is up and the model is loaded/loadable."""
        try:
            with httpx.Client(timeout=3.0) as client:
                r = client.get(f"{self.ollama_url}/models")
                return r.status_code == 200
        except httpx.HTTPError:
            return False


# ---------------------------------------------------------------------------
# Rule-based extractor (fallback / no-LLM dev path)
# ---------------------------------------------------------------------------


_NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "a": 1, "an": 1,
}

_HOUR_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
}
_MINUTE_WORDS = {
    "oh": 0, "o'clock": 0, "fifteen": 15, "thirty": 30, "forty-five": 45,
    "forty five": 45, "ten": 10, "twenty": 20, "twenty-five": 25,
    "twenty five": 25, "five": 5,
}

_NAME_TRIGGERS = ("under", "for", "name is", "name's", "this is", "it's", "i'm", "im")
_NAME_BLOCKLIST = {
    "Bulgogi", "Bibimbap", "Mandu", "Pajeon", "Japchae", "Kimchi", "Boricha",
    "Sejak", "Yuja", "Insam", "Galbi", "Hotteok", "Haemul", "Hearth", "Pass",
    "Vox", "Toast", "Korean", "Locust", "Des", "Moines",
}


class RuleBasedExtractor:
    """Regex-based fallback. Only appends items — can't handle cancel/modify.

    Kept for: unit-testable behavior, offline dev without Ollama, hard
    fallback when LLM is unreachable.
    """

    def extract(self, transcript: list[TranscriptTurn]) -> OrderExtractionResult:
        items_seen: dict[str, ExtractedItem] = {}
        customer_name: str | None = None
        customer_phone: str | None = None
        pickup_time: str | None = None
        caller_finished = False

        for turn in transcript:
            if turn.role != "customer":
                continue
            text = turn.text.lower()

            # Menu items
            for key, entry in MENU_INDEX.items():
                if key in text:
                    qty = self._detect_quantity(turn.text, key)
                    existing = items_seen.get(entry["name"])
                    if existing is None:
                        items_seen[entry["name"]] = ExtractedItem(
                            name=entry["name"], quantity=qty, confirmed=False,
                        )
                    elif qty > existing.quantity:
                        existing.quantity = qty

            # Capture pickup time
            if pickup_time is None:
                pickup_time = self._parse_pickup_time(text)

            # Phone
            m = re.search(r"\b(\d{3})[-.\s]?(\d{3})[-.\s]?(\d{4})\b", turn.text)
            if m and customer_phone is None:
                customer_phone = f"({m.group(1)}) {m.group(2)}-{m.group(3)}"

            # Name
            if customer_name is None:
                customer_name = self._parse_customer_name(turn.text)

            # Caller finished signals
            if any(p in text for p in ("that's it", "that's all", "thanks bye", "thanks, bye", "perfect, thanks")):
                caller_finished = True

        # Confirmation flip — if Vox echoed an item name AFTER customer mentioned it, mark confirmed
        for vox_turn in (t for t in transcript if t.role == "vox"):
            vox_text = vox_turn.text.lower()
            for canonical_name, item in items_seen.items():
                if canonical_name.lower() in vox_text:
                    item.confirmed = True

        return OrderExtractionResult(
            items=list(items_seen.values()),
            customer_name=customer_name,
            customer_phone=customer_phone,
            pickup_time=pickup_time,
            caller_finished=caller_finished,
        )

    def _detect_quantity(self, text: str, dish_name: str) -> int:
        pattern = re.compile(
            rf"(\b\d+\b|\b(?:{'|'.join(_NUMBER_WORDS)})\b)\s+(?:\w+\s+){{0,3}}{re.escape(dish_name)}",
            re.IGNORECASE,
        )
        m = pattern.search(text)
        if not m:
            return 1
        raw = m.group(1).lower()
        if raw.isdigit():
            return max(1, int(raw))
        return _NUMBER_WORDS.get(raw, 1)

    def _parse_pickup_time(self, text_lower: str) -> str | None:
        m = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)\b", text_lower)
        if m:
            hour, minute, ampm = m.groups()
            return f"{int(hour)}:{minute or '00'} {ampm.replace('.', '').upper()}"

        hour_pattern = "|".join(_HOUR_WORDS.keys())
        minute_pattern = "|".join(re.escape(k) for k in sorted(_MINUTE_WORDS.keys(), key=len, reverse=True))
        m = re.search(rf"\b({hour_pattern})(?:\s+({minute_pattern}))?\b", text_lower)
        if m:
            hour_word, minute_word = m.group(1), m.group(2)
            ctx = text_lower[max(0, m.start() - 25): m.end() + 15]
            if not any(sig in ctx for sig in ("pickup", "pick up", "ready", "at ", "by ")):
                return None
            hour = _HOUR_WORDS[hour_word]
            minute = _MINUTE_WORDS.get(minute_word, 0) if minute_word else 0
            ampm = "PM" if 1 <= hour <= 11 else "AM"
            return f"{hour}:{minute:02d} {ampm}"
        return None

    def _parse_customer_name(self, text: str) -> str | None:
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


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


_CACHED_EXTRACTOR: Extractor | None = None


def get_extractor() -> Extractor:
    """Pick an extractor based on env var. Cached after first call.

    VOXREACH_EXTRACTOR=llm   → LLMExtractor (default if Ollama reachable)
    VOXREACH_EXTRACTOR=rule  → RuleBasedExtractor (forced)
    VOXREACH_EXTRACTOR=auto  → try LLM; fall back to rule if Ollama down
    """
    global _CACHED_EXTRACTOR
    if _CACHED_EXTRACTOR is not None:
        return _CACHED_EXTRACTOR

    mode = os.environ.get("VOXREACH_EXTRACTOR", "auto").lower()
    if mode == "rule":
        log.info("extractor: rule-based (forced)")
        _CACHED_EXTRACTOR = RuleBasedExtractor()
    elif mode == "llm":
        log.info("extractor: LLM-based (forced)")
        _CACHED_EXTRACTOR = LLMExtractor()
    else:  # auto
        llm = LLMExtractor()
        if llm.health_check():
            log.info("extractor: LLM-based (Ollama reachable)")
            _CACHED_EXTRACTOR = llm
        else:
            log.warning("extractor: Ollama not reachable, falling back to rule-based")
            _CACHED_EXTRACTOR = RuleBasedExtractor()

    return _CACHED_EXTRACTOR


def reset_cache() -> None:
    """For tests."""
    global _CACHED_EXTRACTOR
    _CACHED_EXTRACTOR = None


# ---------------------------------------------------------------------------
# Transcript-grounded validation — defends against extractor hallucination
# ---------------------------------------------------------------------------


def _customer_text_concat(transcript: list[TranscriptTurn]) -> str:
    """Lowercase concatenation of every CUSTOMER turn in the transcript."""
    return " ".join(t.text for t in transcript if t.role == "customer").lower()


def _item_mentioned_by_customer(item_name: str, customer_text: str) -> bool:
    """Did the customer ever utter a word referring to this item?

    Match heuristic — any of these counts as evidence:
      1. The full canonical name (lowercased) appears as a substring
      2. The first word of the canonical name appears (≥4 chars to skip "the")
      3. A known alias from MENU_INDEX maps to this item
    """
    name_lc = item_name.lower()
    if name_lc in customer_text:
        return True
    # First-word match (bulgogi, japchae, mandu, bibimbap, etc.)
    first = name_lc.split()[0] if name_lc else ""
    if first and len(first) >= 4 and first in customer_text:
        return True
    # Reverse-lookup aliases — find every menu key that points at this item
    for alias, entry in MENU_INDEX.items():
        if entry.get("name", "").lower() == name_lc and len(alias) >= 4 and alias in customer_text:
            return True
    return False


def validate_against_transcript(
    extraction: "OrderExtractionResult",
    transcript: list[TranscriptTurn],
) -> tuple["OrderExtractionResult", list[str]]:
    """Drop items the customer never actually mentioned. Returns (cleaned, warnings).

    Use this AFTER LLMExtractor.extract() but BEFORE applying to call state.
    Defends against the small-model hallucination pattern (most often the
    extractor "adds" the most prominent menu item just because it's in the
    system prompt).

    Returns a copy of the extraction with the bad items removed, plus a list
    of human-readable warnings describing what was dropped — these can be
    surfaced in the UI / logs so the operator knows what the LLM tried to add
    that we caught.
    """
    customer_text = _customer_text_concat(transcript)
    if not customer_text:
        # No customer turns yet — nothing to validate against. Trust the
        # extractor's empty/initial state.
        return extraction, []

    kept = []
    warnings = []
    for item in extraction.items:
        if _item_mentioned_by_customer(item.name, customer_text):
            kept.append(item)
        else:
            warnings.append(
                f"dropped {item.name!r} — no customer utterance mentions it "
                f"(extractor hallucination)"
            )

    if not warnings:
        return extraction, []

    # Build a cleaned copy. We don't mutate the input in case the caller
    # wants to inspect the raw extraction for debugging.
    cleaned = extraction.model_copy(update={"items": kept})
    return cleaned, warnings


def final_order_check(
    final_order_items: list,
    transcript: list[TranscriptTurn],
) -> list[str]:
    """Run on call_end — last-chance sanity check before POS write.

    Walks every item on the final order ticket and confirms there's a
    customer utterance supporting it. Returns a list of suspect items
    by name (empty if all pass). Caller decides whether to BLOCK the
    POS write or just FLAG for human review.
    """
    customer_text = _customer_text_concat(transcript)
    if not customer_text:
        return [it.name for it in final_order_items if it.status != "removed"]

    suspect = []
    for item in final_order_items:
        if item.status == "removed":
            continue
        if not _item_mentioned_by_customer(item.name, customer_text):
            suspect.append(item.name)
    return suspect


# ---------------------------------------------------------------------------
# State diff
# ---------------------------------------------------------------------------


def diff_extractions(
    prev: OrderExtractionResult | None,
    curr: OrderExtractionResult,
) -> "OrderDiff":  # noqa: F821
    """Compute the granular event diff between two consecutive extractions."""
    from schema import OrderDiff  # local import to avoid module-load cycle

    prev_items = {i.name: i for i in (prev.items if prev else [])}
    curr_items = {i.name: i for i in curr.items}

    added = [i for n, i in curr_items.items() if n not in prev_items]
    removed = [i for n, i in prev_items.items() if n not in curr_items]

    qty_changed: list[tuple[str, int, int]] = []
    mod_changed: list[tuple[str, str | None, str | None]] = []
    spice_changed: list[tuple[str, str | None, str | None]] = []
    notes_changed: list[tuple[str, str | None, str | None]] = []
    confirmed: list[str] = []
    for name, curr_item in curr_items.items():
        if name not in prev_items:
            continue
        prev_item = prev_items[name]
        if curr_item.quantity != prev_item.quantity:
            qty_changed.append((name, prev_item.quantity, curr_item.quantity))
        if curr_item.modifier != prev_item.modifier:
            mod_changed.append((name, prev_item.modifier, curr_item.modifier))
        if curr_item.spice_level != prev_item.spice_level:
            spice_changed.append((name, prev_item.spice_level, curr_item.spice_level))
        if curr_item.notes != prev_item.notes:
            notes_changed.append((name, prev_item.notes, curr_item.notes))
        if curr_item.confirmed and not prev_item.confirmed:
            confirmed.append(name)

    return OrderDiff(
        added=added,
        removed=removed,
        quantity_changed=qty_changed,
        modifier_changed=mod_changed,
        spice_changed=spice_changed,
        notes_changed=notes_changed,
        confirmed=confirmed,
    )
