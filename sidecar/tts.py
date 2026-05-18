"""Order-readback TTS using Piper.

Decoupled from moshi entirely — runs on CPU via ONNX runtime, takes ~300ms
to generate a 10-second readback. moshi keeps its GPU for the live duplex
conversation; this fills the "verify the kitchen ticket" UX need without
fighting moshi for real-time frame budget.

Voice files live at /workspace/.cache/piper-voices/ (downloaded by setup.sh).
Override the voice via VOXREACH_TTS_VOICE env (default: en_US-amy-medium).
"""

from __future__ import annotations

import io
import logging
import os
import threading
import wave
from pathlib import Path

from schema import OrderTicket

log = logging.getLogger("voxreach.tts")

VOICES_DIR = Path(os.environ.get("VOXREACH_TTS_VOICES_DIR", "/workspace/.cache/piper-voices"))
DEFAULT_VOICE = os.environ.get("VOXREACH_TTS_VOICE", "en_US-amy-medium")


class _PiperSingleton:
    """Lazy-loaded Piper voice. One voice per process is plenty for our case."""

    _lock = threading.Lock()
    _instance: "_PiperSingleton | None" = None

    def __init__(self, voice_name: str):
        from piper.voice import PiperVoice

        onnx_path = VOICES_DIR / f"{voice_name}.onnx"
        config_path = VOICES_DIR / f"{voice_name}.onnx.json"
        if not onnx_path.exists() or not config_path.exists():
            raise FileNotFoundError(
                f"Piper voice files not found at {onnx_path}. "
                f"Run setup.sh or download manually from https://huggingface.co/rhasspy/piper-voices"
            )
        log.info("loading Piper voice %s ...", voice_name)
        self.voice = PiperVoice.load(str(onnx_path), config_path=str(config_path))
        log.info("Piper voice loaded (sample_rate=%d)", self.voice.config.sample_rate)
        self.sample_rate = self.voice.config.sample_rate

    @classmethod
    def get(cls, voice_name: str | None = None) -> "_PiperSingleton":
        name = voice_name or DEFAULT_VOICE
        with cls._lock:
            if cls._instance is None or getattr(cls._instance, "_voice_name", None) != name:
                inst = cls(name)
                inst._voice_name = name
                cls._instance = inst
            return cls._instance

    def synth_wav_bytes(self, text: str) -> bytes:
        """Synthesize `text` and return WAV bytes (mono, 22050 Hz typically).

        Piper 1.4+ changed the API:
          - synthesize(text)        -> Iterable[AudioChunk]  (need to iterate ourselves)
          - synthesize_wav(text, f) -> writes WAV format directly to f
        Prefer synthesize_wav when available; fall back to manual streaming.
        """
        if not text.strip():
            raise ValueError("empty text passed to TTS")
        buf = io.BytesIO()
        synth_wav = getattr(self.voice, "synthesize_wav", None)
        if callable(synth_wav):
            # Newer API — writes the full WAV (header + frames) to the file.
            synth_wav(text, buf)
            return buf.getvalue()
        # Fallback for older Piper versions: iterate AudioChunk and write
        # 16-bit PCM frames into a wave we open ourselves.
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(self.sample_rate)
            for chunk in self.voice.synthesize(text):
                # Different Piper builds expose the int16 bytes under different attrs
                raw = (
                    getattr(chunk, "audio_int16_bytes", None)
                    or getattr(chunk, "audio_int16", None)
                    or getattr(chunk, "audio", None)
                )
                if raw is None:
                    continue
                wf.writeframes(raw if isinstance(raw, (bytes, bytearray)) else bytes(raw))
        return buf.getvalue()


# Number-to-word helpers — Piper handles numerals OK but spelling them out
# matches Vox's own delivery style (one bulgogi, nineteen dollars).
_ONES = [
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
    "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen",
    "seventeen", "eighteen", "nineteen",
]
_TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]


def _int_to_words(n: int) -> str:
    if n < 0:
        return "negative " + _int_to_words(-n)
    if n < 20:
        return _ONES[n]
    if n < 100:
        t, o = divmod(n, 10)
        return _TENS[t] + ("" if o == 0 else "-" + _ONES[o])
    if n < 1000:
        h, r = divmod(n, 100)
        return _ONES[h] + " hundred" + ("" if r == 0 else " " + _int_to_words(r))
    # Beyond 1000 we just fall back to digits — restaurant orders won't go there
    return str(n)


def _cents_to_words(cents: int) -> str:
    """Render $19 as 'nineteen dollars', $19.50 as 'nineteen dollars and fifty cents'."""
    dollars, rem_cents = divmod(cents, 100)
    parts = [_int_to_words(dollars), "dollar" if dollars == 1 else "dollars"]
    if rem_cents:
        parts += ["and", _int_to_words(rem_cents), "cent" if rem_cents == 1 else "cents"]
    return " ".join(parts)


def compose_readback(order: OrderTicket) -> str:
    """Build the readback sentence from the live order state.

    Skips removed items, uses spoken-out numbers/prices to match Vox's style,
    and ends with the verification ask ("does that look right?").
    """
    active = order.active_items
    if not active:
        return "The order ticket is empty. Nothing to read back yet."

    lines: list[str] = []
    lines.append("Here is the order on the ticket so far.")

    for item in active:
        qty = _int_to_words(item.quantity) if item.quantity < 20 else str(item.quantity)
        price = _cents_to_words(item.line_total_cents)
        clause = f"{qty} {item.name}, {price}"
        extras: list[str] = []
        if item.modifier:
            extras.append(item.modifier)
        if item.spice_level:
            extras.append(item.spice_level)
        if item.notes:
            extras.append(item.notes)
        if extras:
            clause += ", " + ", ".join(extras)
        lines.append(clause + ".")

    lines.append(f"Total: {_cents_to_words(order.subtotal_cents)}.")

    if order.customer_name:
        lines.append(f"Under the name {order.customer_name}.")
    if order.pickup_time:
        lines.append(f"Pickup at {order.pickup_time}.")
    if order.customer_phone:
        digits = "".join(ch for ch in order.customer_phone if ch.isdigit())
        if len(digits) >= 7:
            spaced = " ".join(_ONES[int(d)] for d in digits)
            lines.append(f"Callback number: {spaced}.")

    lines.append("Does that look right?")
    return " ".join(lines)


def synth_readback(order: OrderTicket, voice: str | None = None) -> tuple[bytes, str]:
    """High-level: compose text from the order, synthesize, return (wav_bytes, text)."""
    text = compose_readback(order)
    piper = _PiperSingleton.get(voice)
    wav = piper.synth_wav_bytes(text)
    return wav, text
