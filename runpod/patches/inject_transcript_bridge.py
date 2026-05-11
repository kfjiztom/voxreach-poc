"""Idempotent patcher for moshi/server.py — forward transcripts to the VoxReach sidecar.

Adds three things to NVIDIA's PersonaPlex moshi.server:

1. Module-level helpers — fire-and-forget HTTP POST to the sidecar so the audio
   path is never blocked by a slow network call.
2. `voxreach_call_start()` invoked when a WebSocket session reaches handshake.
3. `voxreach_call_end()` invoked when the session closes.
4. `voxreach_transcript("vox", _text)` invoked every time the model emits a
   text token (Vox's spoken words).

Why only Vox's text and not the customer's:
  Moshi's text stream at `tokens[0, 0, 0]` represents the model's spoken
  output. The user's speech audio is processed but not separately transcribed
  in this code path. Vox's persona prompt requires it to read back every
  ordered item ("one bulgogi at nineteen, one haemul pajeon at thirteen…"),
  so the order extractor can derive the order from Vox's confirmations alone.
  Adding a separate customer-side ASR is Phase B work.

Idempotent — each injection point checks for a marker before modifying.

Usage:
    python inject_transcript_bridge.py <path-to-server.py>
"""

from __future__ import annotations

import sys
from pathlib import Path

MARKER_HELPERS = "# VoxReach transcript bridge helpers"
MARKER_TRANSCRIPT = "# VoxReach: forward Vox's text"
MARKER_START = "# VoxReach: notify call_start"
MARKER_END = "# VoxReach: notify call_end"

# ---------------------------------------------------------------------------
# Injection 1 — module-level helpers, added after the existing imports.
# ---------------------------------------------------------------------------

ANCHOR_AFTER_IMPORTS = (
    "from .utils.logging import setup_logger, ColorizedLog"
)

HELPERS_BLOCK = '''

# VoxReach transcript bridge helpers
import json as _vox_json
import threading as _vox_threading
import urllib.request as _vox_urllib_request


def _voxreach_post(path, payload, timeout=1.0):
    """Fire-and-forget POST to the VoxReach sidecar.

    Never blocks the audio path; failures are silently swallowed and logged
    at DEBUG. The sidecar URL is read at call time from VOXREACH_SIDECAR_URL
    (default http://localhost:8001).
    """
    def _do():
        sidecar = os.environ.get("VOXREACH_SIDECAR_URL", "http://localhost:8001")
        if not sidecar:
            return
        try:
            data = _vox_json.dumps(payload).encode("utf-8")
            req = _vox_urllib_request.Request(
                f"{sidecar}{path}",
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            _vox_urllib_request.urlopen(req, timeout=timeout).read()
        except Exception:
            # Stay quiet — never spam the audio-handling thread's stderr
            pass
    _vox_threading.Thread(target=_do, daemon=True).start()


def voxreach_call_start():
    _voxreach_post("/api/call/start", {})


def voxreach_call_end():
    _voxreach_post("/api/call/end", {})


def voxreach_transcript(role, text):
    if not text:
        return
    cleaned = text.strip()
    if not cleaned:
        return
    _voxreach_post("/api/transcript", {"role": role, "text": text})

# end VoxReach transcript bridge helpers
'''

# ---------------------------------------------------------------------------
# Injection 2 — call voxreach_transcript("vox", _text) right after the text
# token is sent to the client. We anchor on the existing `await ws.send_bytes(msg)`
# line inside opus_loop().
# ---------------------------------------------------------------------------

ANCHOR_TEXT_SEND = '                            msg = b"\\x02" + bytes(_text, encoding="utf8")\n                            await ws.send_bytes(msg)'

TRANSCRIPT_INJECT = '''                            msg = b"\\x02" + bytes(_text, encoding="utf8")
                            await ws.send_bytes(msg)
                            # VoxReach: forward Vox's text to the sidecar for order extraction
                            voxreach_transcript("vox", _text)'''

# ---------------------------------------------------------------------------
# Injection 3 — voxreach_call_start right at the handshake. We anchor on the
# existing `await ws.send_bytes(b"\\x00")` (the handshake byte sent to client).
# ---------------------------------------------------------------------------

ANCHOR_HANDSHAKE = '                await ws.send_bytes(b"\\x00")\n                clog.log("info", "sent handshake bytes")'

START_INJECT = '''                await ws.send_bytes(b"\\x00")
                clog.log("info", "sent handshake bytes")
                # VoxReach: notify call_start (fire-and-forget)
                voxreach_call_start()'''

# ---------------------------------------------------------------------------
# Injection 4 — voxreach_call_end at session close. Anchor on
# `clog.log("info", "session closed")`.
# ---------------------------------------------------------------------------

ANCHOR_SESSION_CLOSED = '                clog.log("info", "session closed")'

END_INJECT = '''                clog.log("info", "session closed")
                # VoxReach: notify call_end (fire-and-forget)
                voxreach_call_end()'''


# ---------------------------------------------------------------------------
# Patcher
# ---------------------------------------------------------------------------


def patch(server_py: Path) -> dict:
    """Returns a dict of (injection_name, 'patched'|'already') for each."""
    if not server_py.exists():
        raise FileNotFoundError(f"server.py not found at {server_py}")

    text = server_py.read_text()
    results: dict[str, str] = {}

    # 1) Helpers block
    if MARKER_HELPERS in text:
        results["helpers"] = "already"
    else:
        if ANCHOR_AFTER_IMPORTS not in text:
            raise ValueError(f"Could not find anchor for helpers: {ANCHOR_AFTER_IMPORTS!r}")
        text = text.replace(ANCHOR_AFTER_IMPORTS, ANCHOR_AFTER_IMPORTS + HELPERS_BLOCK, 1)
        results["helpers"] = "patched"

    # 2) Transcript forward (inside opus_loop)
    if MARKER_TRANSCRIPT in text:
        results["transcript"] = "already"
    else:
        if ANCHOR_TEXT_SEND not in text:
            raise ValueError(f"Could not find anchor for transcript: {ANCHOR_TEXT_SEND!r}")
        text = text.replace(ANCHOR_TEXT_SEND, TRANSCRIPT_INJECT, 1)
        results["transcript"] = "patched"

    # 3) Call start at handshake
    if MARKER_START in text:
        results["call_start"] = "already"
    else:
        if ANCHOR_HANDSHAKE not in text:
            raise ValueError(f"Could not find anchor for call_start: {ANCHOR_HANDSHAKE!r}")
        text = text.replace(ANCHOR_HANDSHAKE, START_INJECT, 1)
        results["call_start"] = "patched"

    # 4) Call end at session close
    if MARKER_END in text:
        results["call_end"] = "already"
    else:
        if ANCHOR_SESSION_CLOSED not in text:
            raise ValueError(f"Could not find anchor for call_end: {ANCHOR_SESSION_CLOSED!r}")
        text = text.replace(ANCHOR_SESSION_CLOSED, END_INJECT, 1)
        results["call_end"] = "patched"

    server_py.write_text(text)
    return results


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: python inject_transcript_bridge.py <path-to-server.py>", file=sys.stderr)
        sys.exit(2)
    results = patch(Path(sys.argv[1]))
    for name, status in results.items():
        marker = "✓" if status == "patched" else "•"
        print(f"  {marker} {name}: {status}")
