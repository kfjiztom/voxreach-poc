"""Idempotent patcher for moshi/server.py — embed Kyutai STT for customer voice.

Adds three things on top of the existing transcript-bridge patch:

1. Helpers block (VAD + STT worker + sidecar POST) — see customer_stt_helpers.py
   for the standalone source. We inline it here because moshi.server lives in
   a different venv from the sidecar and we don't want to add a packaging
   step just to share one file.

2. Hook in opus_loop right after the inbound PCM is decoded:
       pcm = opus_reader.read_pcm()
       if pcm.shape[-1] == 0:
           continue
       voxreach_customer_audio(pcm)   # <- injected

3. Reorders the existing call_end injection so the STT flush runs BEFORE
   voxreach_call_end() — otherwise the customer's last spoken words can
   arrive at the sidecar after /api/call/end has already triggered the
   forced final extraction.

Idempotent: each injection point checks for a marker before modifying.

This patch DEPENDS ON inject_transcript_bridge.py having run first — it
reuses `_voxreach_post` from that block to POST customer transcripts.

Usage:
    python inject_customer_stt.py <path-to-server.py>
"""

from __future__ import annotations

import sys
from pathlib import Path

from customer_stt_helpers import HELPERS_BLOCK as STT_HELPERS_BLOCK

MARKER_STT_HELPERS = "# VoxReach customer STT bridge v1"
MARKER_STT_HOOK = "# VoxReach STT: forward customer PCM"
MARKER_STT_FLUSH = "# VoxReach STT: flush remaining customer audio"
MARKER_BRIDGE_HELPERS_END = "# end VoxReach transcript bridge helpers"
MARKER_BRIDGE_CALL_END = (
    "                # VoxReach: notify call_end (fire-and-forget)\n"
    "                voxreach_call_end()"
)

# ---------------------------------------------------------------------------
# Injection 1 — STT helpers, placed AFTER the existing transcript-bridge
# helpers so `_voxreach_post` is in scope.
# ---------------------------------------------------------------------------

# We anchor on the bridge's end marker and append our block after it. Done
# this way (rather than after a `# end` line directly above) so both blocks
# stay grouped and easy to read in source.

# ---------------------------------------------------------------------------
# Injection 2 — audio hook in opus_loop.
# ---------------------------------------------------------------------------

ANCHOR_OPUS_READ = (
    "                pcm = opus_reader.read_pcm()\n"
    "                if pcm.shape[-1] == 0:\n"
    "                    continue"
)

OPUS_INJECT = (
    "                pcm = opus_reader.read_pcm()\n"
    "                if pcm.shape[-1] == 0:\n"
    "                    continue\n"
    "                # VoxReach STT: forward customer PCM (24 kHz, mono, float32 numpy)\n"
    "                voxreach_customer_audio(pcm)"
)

# ---------------------------------------------------------------------------
# Injection 3 — reorder call_end so STT flush runs first.
# ---------------------------------------------------------------------------

ANCHOR_BRIDGE_CALL_END = MARKER_BRIDGE_CALL_END
FLUSH_BEFORE_CALL_END = (
    "                # VoxReach STT: flush remaining customer audio (blocks up to 5s)\n"
    "                voxreach_customer_stt_flush()\n"
    "                # VoxReach: notify call_end (fire-and-forget)\n"
    "                voxreach_call_end()"
)


def patch(server_py: Path) -> dict:
    """Return {injection: 'patched'|'already'|'error: ...'}."""
    if not server_py.exists():
        raise FileNotFoundError(f"server.py not found at {server_py}")

    text = server_py.read_text()
    results: dict[str, str] = {}

    # 1) STT helpers block — append after the transcript-bridge helpers block
    if MARKER_STT_HELPERS in text:
        results["stt_helpers"] = "already"
    else:
        if MARKER_BRIDGE_HELPERS_END not in text:
            raise ValueError(
                "Cannot inject STT helpers — bridge helpers block not found. "
                "Run inject_transcript_bridge.py first."
            )
        # Append our block immediately after the bridge's end marker
        text = text.replace(
            MARKER_BRIDGE_HELPERS_END,
            MARKER_BRIDGE_HELPERS_END + STT_HELPERS_BLOCK,
            1,
        )
        results["stt_helpers"] = "patched"

    # 2) Audio hook in opus_loop
    if MARKER_STT_HOOK in text:
        results["audio_hook"] = "already"
    else:
        if ANCHOR_OPUS_READ not in text:
            raise ValueError(
                "Could not find anchor for audio hook. The moshi version "
                "may have changed read_pcm() — check opus_loop() in server.py."
            )
        text = text.replace(ANCHOR_OPUS_READ, OPUS_INJECT, 1)
        results["audio_hook"] = "patched"

    # 3) Flush before call_end
    if MARKER_STT_FLUSH in text:
        results["flush_before_end"] = "already"
    else:
        if ANCHOR_BRIDGE_CALL_END not in text:
            raise ValueError(
                "Could not find bridge's call_end injection. Make sure the "
                "transcript bridge patch ran cleanly first."
            )
        text = text.replace(ANCHOR_BRIDGE_CALL_END, FLUSH_BEFORE_CALL_END, 1)
        results["flush_before_end"] = "patched"

    server_py.write_text(text)
    return results


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: python inject_customer_stt.py <path-to-server.py>", file=sys.stderr)
        sys.exit(2)
    results = patch(Path(sys.argv[1]))
    for name, status in results.items():
        marker = "✓" if status == "patched" else "•"
        print(f"  {marker} {name}: {status}")
