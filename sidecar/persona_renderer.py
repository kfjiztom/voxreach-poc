"""Time-aware persona renderer.

moshi.server reads the persona file at the start of each WS session (per
our inject_default_prompt patch). So if we keep that file fresh — re-rendered
every N seconds with current time, day, kitchen-open status, and the latest
pickup time we can still promise — every new call gets a Vox who knows what
time it is.

That's the trick that makes "kitchen closes in 17 minutes" + pickup-time
validation work without changing moshi at all.

Background task: render_persona_loop() runs every 30s and writes the file.
Call render_persona_now() once at sidecar startup to seed the file before
any traffic.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

log = logging.getLogger("voxreach.persona")

# Inputs / outputs
KNOWLEDGE_PATH = Path(__file__).parent.parent / "knowledge" / "hearth_and_pass.json"
TEMPLATE_PATH = Path(__file__).parent.parent / "persona" / "vox_personaplex_prompt_template.txt"
OUTPUT_PATH = Path(__file__).parent.parent / "persona" / "vox_personaplex_prompt_compact.txt"

# The restaurant's local timezone. Override with VOXREACH_RESTAURANT_TZ if
# the demo lives somewhere other than Des Moines.
RESTAURANT_TZ = ZoneInfo(os.environ.get("VOXREACH_RESTAURANT_TZ", "America/Chicago"))

# How many minutes before posted close the kitchen actually stops cooking.
# Matches the "kitchen_closes" field in the knowledge file ("30 minutes
# before posted close") — also the latest we can promise pickup.
KITCHEN_CLOSE_BUFFER_MIN = 30

# Re-render cadence. Short enough that "closes in N minutes" stays accurate
# across the typical 2-3 min call duration.
RENDER_INTERVAL_SEC = 30


_DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def _parse_hours(hours_str: str) -> tuple[int, int] | None:
    """Parse '11am – 9pm' or '11am – 10pm' → (open_minute, close_minute)
    where each minute value is minutes-since-midnight in 24h time.
    Returns None for 'Closed' or unparseable values."""
    if not hours_str or hours_str.lower().strip() == "closed":
        return None
    # Normalize unicode em-dash / en-dash to "-"
    s = hours_str.replace("–", "-").replace("—", "-").lower()
    parts = [p.strip() for p in s.split("-")]
    if len(parts) != 2:
        return None
    try:
        open_min = _parse_time_to_minutes(parts[0])
        close_min = _parse_time_to_minutes(parts[1])
        return open_min, close_min
    except Exception:
        return None


def _parse_time_to_minutes(s: str) -> int:
    """'11am' / '9pm' / '10:30pm' → minutes since midnight (24h)."""
    s = s.strip().lower().replace(" ", "")
    pm = s.endswith("pm")
    am = s.endswith("am")
    body = s[:-2] if (pm or am) else s
    if ":" in body:
        h, m = body.split(":")
        hour, minute = int(h), int(m)
    else:
        hour, minute = int(body), 0
    if pm and hour != 12:
        hour += 12
    elif am and hour == 12:
        hour = 0
    return hour * 60 + minute


def _format_time_friendly(minutes_since_midnight: int) -> str:
    """120 → '2:00 AM', 1290 → '9:30 PM'."""
    h, m = divmod(minutes_since_midnight, 60)
    period = "AM" if h < 12 else "PM"
    display = h % 12 or 12
    return f"{display}:{m:02d} {period}"


def _compute_status_block(now: datetime, hours: dict) -> tuple[str, str, str]:
    """Returns (day_name_title, time_str, status_line, latest_pickup_str)."""
    day_key = _DAYS[now.weekday()]
    today_hours = hours.get(day_key, "")
    parsed = _parse_hours(today_hours)
    now_min = now.hour * 60 + now.minute
    time_str = _format_time_friendly(now_min)

    if parsed is None:
        # Closed all day
        return (
            day_key.title(),
            time_str,
            "We are CLOSED today (Mondays are dark). Politely tell callers we open tomorrow.",
            "n/a — we're closed today",
        )

    open_min, close_min = parsed
    latest_pickup_min = max(open_min, close_min - KITCHEN_CLOSE_BUFFER_MIN)
    latest_pickup_str = _format_time_friendly(latest_pickup_min)

    if now_min < open_min:
        mins_to_open = open_min - now_min
        status = (
            f"NOT OPEN YET. We open at {_format_time_friendly(open_min)} "
            f"({mins_to_open} min from now). Kitchen closes at "
            f"{_format_time_friendly(close_min)}."
        )
    elif now_min >= close_min:
        status = (
            f"CLOSED for the night. Posted close was "
            f"{_format_time_friendly(close_min)}. Do not take new orders for tonight."
        )
        latest_pickup_str = "we're already closed — push to tomorrow's open"
    elif now_min >= latest_pickup_min:
        mins_to_close = close_min - now_min
        status = (
            f"OPEN but kitchen stops cooking very soon ({mins_to_close} min). "
            f"No new pickup orders can be promised after {latest_pickup_str}."
        )
    else:
        mins_to_close = close_min - now_min
        status = (
            f"OPEN. Posted close: {_format_time_friendly(close_min)} "
            f"({mins_to_close} min from now). Kitchen stops cooking 30 min before."
        )

    return (day_key.title(), time_str, status, latest_pickup_str)


def render_persona_now() -> bool:
    """Render the persona file once. Returns True on success."""
    try:
        if not TEMPLATE_PATH.exists():
            log.warning("persona template missing at %s — skipping render", TEMPLATE_PATH)
            return False
        knowledge = json.loads(KNOWLEDGE_PATH.read_text())
        hours = knowledge.get("hours", {})

        now = datetime.now(RESTAURANT_TZ)
        day, time_str, status, latest_pickup = _compute_status_block(now, hours)

        template = TEMPLATE_PATH.read_text()
        rendered = (template
                    .replace("{{DAY}}", day)
                    .replace("{{TIME}}", time_str)
                    .replace("{{STATUS}}", status)
                    .replace("{{LATEST_PICKUP}}", latest_pickup))

        # Atomic write — rename is the only way to avoid moshi reading a
        # half-written file mid-render. Write to a sibling temp file then
        # rename over the target.
        tmp = OUTPUT_PATH.with_suffix(".tmp")
        tmp.write_text(rendered)
        tmp.replace(OUTPUT_PATH)
        log.debug("rendered time-aware persona for %s %s — %s", day, time_str, status[:60])
        return True
    except Exception as e:
        log.error("persona render failed: %s", e)
        return False


async def render_persona_loop():
    """Background task — re-renders the persona every RENDER_INTERVAL_SEC."""
    # First render is sync via render_persona_now() at startup; loop handles refresh.
    while True:
        await asyncio.sleep(RENDER_INTERVAL_SEC)
        await asyncio.to_thread(render_persona_now)
