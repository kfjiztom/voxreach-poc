"""Idempotent patcher for moshi/server.py — forward transcripts to the VoxReach sidecar.

Adds these things to NVIDIA's PersonaPlex moshi.server:

1. Module-level helpers — fire-and-forget HTTP POST to the sidecar so the audio
   path is never blocked by a slow network call.
2. **Sentence-level buffering** for Vox's text tokens before POSTing to the
   sidecar. Moshi emits one text token per ~80ms frame (so "bulgogi" arrives
   as 3-7 separate tokens). Without buffering, the sidecar gets per-syllable
   POSTs and the LLM extractor runs against fragmented context. With
   buffering, the sidecar only sees complete sentences (or every ~80 chars
   if no punctuation arrives).
3. `voxreach_call_start()` invoked when a WebSocket session reaches handshake.
4. `voxreach_call_end()` flushes any pending buffer and notifies sidecar.
5. `voxreach_transcript(role, text)` invoked every time the model emits a
   text token (Vox's spoken words) — buffers Vox; passes others through.

What this DOES NOT change:
  The WebSocket text messages to the client (`b"\\x02" + utf8`) are still
  per-token. NVIDIA's UI gets the typewriter effect it expects. Only the
  sidecar POST is buffered. To also batch the WebSocket sends would require
  more invasive changes and risk breaking the front-end animation.

Why only Vox's text and not the customer's:
  Moshi's text stream at `tokens[0, 0, 0]` represents the model's spoken
  output. The user's speech audio is processed but not separately transcribed
  in this code path. Vox's persona requires it to read back each ordered
  item ("one bulgogi at nineteen…"), so the order extractor can derive the
  order from Vox's confirmations. Customer-side ASR (Kyutai STT) is Phase B.

Idempotent — each injection point checks for a marker before modifying.
This version replaces the legacy non-buffered transcript helpers in place.

Usage:
    python inject_transcript_bridge.py <path-to-server.py>
"""

from __future__ import annotations

import sys
from pathlib import Path

MARKER_HELPERS = "# VoxReach transcript bridge helpers"
MARKER_BUFFERED = "# VoxReach: buffered transcript v2"
MARKER_TRANSCRIPT = "# VoxReach: forward Vox's text"
MARKER_START = "# VoxReach: notify call_start"
MARKER_END = "# VoxReach: notify call_end"

# ---------------------------------------------------------------------------
# Injection 1 — module-level helpers, added after the existing imports.
# v2: includes sentence-buffer for Vox's text + flush logic.
# ---------------------------------------------------------------------------

ANCHOR_AFTER_IMPORTS = (
    "from .utils.logging import setup_logger, ColorizedLog"
)

HELPERS_BLOCK = '''

# VoxReach transcript bridge helpers
# VoxReach: buffered transcript v2
import json as _vox_json
import threading as _vox_threading
import urllib.request as _vox_urllib_request


def _voxreach_post(path, payload, timeout=1.0):
    """Fire-and-forget POST to the VoxReach sidecar.

    Never blocks the audio path; failures are silently swallowed.
    The sidecar URL is read at call time from VOXREACH_SIDECAR_URL
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
            pass
    _vox_threading.Thread(target=_do, daemon=True).start()


# Sentence-level buffering for Vox's text stream. Moshi emits one text
# token per ~80ms audio frame; each token can be a syllable, word, or
# punctuation fragment. Without buffering, the sidecar gets POST-per-syllable
# which destroys LLM extraction quality. We accumulate until:
#   * sentence-terminal punctuation (. ! ?)
#   * comma boundary if buffer >= 25 chars
#   * forced flush at 80+ chars to avoid run-on
#   * silence frame OR call_end (flush remaining)
_VOX_BUFFER_LOCK = _vox_threading.Lock()
_vox_text_buffer = ""


def _vox_flush_buffer_locked():
    """Caller must hold _VOX_BUFFER_LOCK."""
    global _vox_text_buffer
    text = _vox_text_buffer.strip()
    _vox_text_buffer = ""
    if not text:
        return
    _voxreach_post("/api/transcript", {"role": "vox", "text": text})


def voxreach_call_start():
    global _vox_text_buffer
    with _VOX_BUFFER_LOCK:
        _vox_text_buffer = ""
    _voxreach_post("/api/call/start", {})


def voxreach_call_end():
    # Flush any pending buffer before signaling call_end
    with _VOX_BUFFER_LOCK:
        _vox_flush_buffer_locked()
    _voxreach_post("/api/call/end", {})


def voxreach_silence():
    """Called on silence/PAD frames — natural place to flush the buffer."""
    with _VOX_BUFFER_LOCK:
        if _vox_text_buffer.strip():
            _vox_flush_buffer_locked()


def voxreach_transcript(role, text):
    """Customer turns POST immediately. Vox turns buffer to sentence boundary."""
    if not text:
        return
    if role != "vox":
        _voxreach_post("/api/transcript", {"role": role, "text": text})
        return

    global _vox_text_buffer
    with _VOX_BUFFER_LOCK:
        _vox_text_buffer += text
        buf = _vox_text_buffer
        stripped = buf.rstrip()
        should_flush = False
        if stripped.endswith((".", "!", "?")):
            should_flush = True
        elif len(buf) >= 25 and stripped.endswith((",", ":", ";")):
            should_flush = True
        elif len(buf) >= 80:
            should_flush = True
        if should_flush:
            _vox_flush_buffer_locked()

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
# Injection 3 — voxreach_call_start right at the handshake.
# ---------------------------------------------------------------------------

ANCHOR_HANDSHAKE = '                await ws.send_bytes(b"\\x00")\n                clog.log("info", "sent handshake bytes")'

START_INJECT = '''                await ws.send_bytes(b"\\x00")
                clog.log("info", "sent handshake bytes")
                # VoxReach: notify call_start (fire-and-forget)
                voxreach_call_start()'''

# ---------------------------------------------------------------------------
# Injection 4 — voxreach_call_end at session close.
# ---------------------------------------------------------------------------

ANCHOR_SESSION_CLOSED = '                clog.log("info", "session closed")'

END_INJECT = '''                clog.log("info", "session closed")
                # VoxReach: notify call_end (fire-and-forget)
                voxreach_call_end()'''


# ---------------------------------------------------------------------------
# Patcher — supports upgrading from the v1 (non-buffered) helpers block
# ---------------------------------------------------------------------------


def patch(server_py: Path) -> dict:
    """Returns a dict of (injection_name, 'patched'|'already'|'upgraded') for each."""
    if not server_py.exists():
        raise FileNotFoundError(f"server.py not found at {server_py}")

    text = server_py.read_text()
    results: dict[str, str] = {}

    # 1) Helpers block — if v1 is present (no MARKER_BUFFERED), upgrade in place
    if MARKER_HELPERS in text:
        if MARKER_BUFFERED in text:
            results["helpers"] = "already"
        else:
            # Locate and replace the entire v1 helpers block
            start = text.find(MARKER_HELPERS)
            end_marker = "# end VoxReach transcript bridge helpers"
            end = text.find(end_marker, start)
            if start == -1 or end == -1:
                # Couldn't find boundary — just append the new block
                results["helpers"] = "error: legacy block found but boundary missing"
            else:
                end_full = end + len(end_marker)
                # Strip the new HELPERS_BLOCK's leading "\n\n" so we don't grow blank lines
                replacement = HELPERS_BLOCK.lstrip("\n")
                text = text[:start] + replacement.rstrip("\n") + text[end_full:]
                results["helpers"] = "upgraded"
    else:
        if ANCHOR_AFTER_IMPORTS not in text:
            raise ValueError(f"Could not find anchor for helpers: {ANCHOR_AFTER_IMPORTS!r}")
        text = text.replace(ANCHOR_AFTER_IMPORTS, ANCHOR_AFTER_IMPORTS + HELPERS_BLOCK, 1)
        results["helpers"] = "patched"

    # 2) Transcript forward (inside opus_loop) — unchanged from v1
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
        marker = "✓" if status in ("patched", "upgraded") else "•"
        print(f"  {marker} {name}: {status}")
