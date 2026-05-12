"""Customer-side STT helpers — embedded inside moshi.server via patch.

This module is NOT imported in the moshi server directly. Instead, the
contents of HELPERS_BLOCK below are inlined into moshi/server.py by the
inject_customer_stt.py patcher, alongside the existing VoxReach
transcript-bridge helpers.

Why inline rather than import: moshi.server lives in a different venv from
the sidecar (it runs inside the PersonaPlex venv). Inlining lets us avoid
adding a fragile import path / packaging dance.

This file exists primarily so:
  1. The injected code is reviewable in isolation in source control.
  2. We can unit-test the VAD + buffer logic without needing moshi installed.

The injected module assumes:
  - moshi feeds us PCM at 24 kHz, mono, float32 in [-1, 1].
    (If moshi uses a different rate, we resample here.)
  - HF cache + persona env vars are already set when moshi.server starts.
  - The VoxReach transcript-bridge helpers (_voxreach_post etc.) are also
    present in the same module — we depend on _voxreach_post() to ship
    customer transcripts to the sidecar.
"""

from __future__ import annotations

# The exact text written into moshi.server below the existing bridge helpers.
# Marker on the first line so the patcher can detect idempotency.
HELPERS_BLOCK = r'''

# VoxReach customer STT bridge v1
import collections as _vox_collections
import numpy as _vox_np
import os as _vox_os_stt
import queue as _vox_queue
import threading as _vox_threading_stt
import time as _vox_time_stt

_VOX_STT_ENABLED = _vox_os_stt.environ.get("VOXREACH_CUSTOMER_STT_ENABLED", "1") == "1"
_VOX_STT_MODEL_ID = _vox_os_stt.environ.get("VOXREACH_STT_MODEL", "kyutai/stt-2.6b-en")
_VOX_VAD_AGGRESSIVENESS = int(_vox_os_stt.environ.get("VOXREACH_VAD_AGGRESSIVENESS", "2"))
_VOX_VAD_SILENCE_MS = int(_vox_os_stt.environ.get("VOXREACH_VAD_SILENCE_MS", "500"))
_VOX_VAD_MIN_UTT_MS = int(_vox_os_stt.environ.get("VOXREACH_VAD_MIN_UTT_MS", "300"))
_VOX_VAD_MAX_UTT_MS = int(_vox_os_stt.environ.get("VOXREACH_VAD_MAX_UTT_MS", "15000"))
_VOX_INPUT_SAMPLE_RATE = int(_vox_os_stt.environ.get("VOXREACH_MOSHI_INPUT_RATE", "24000"))

# webrtcvad wants 8/16/32/48 kHz; we resample to 16 kHz for the VAD check only.
# The STT model receives the original 24 kHz audio.
_VOX_VAD_SAMPLE_RATE = 16000
_VOX_VAD_FRAME_MS = 20  # 20ms frames are the most accurate for webrtcvad
_VOX_VAD_FRAME_LEN = _VOX_VAD_SAMPLE_RATE * _VOX_VAD_FRAME_MS // 1000  # 320 samples

# Lazy globals — set on first use, never re-initialized.
_vox_stt_vad = None  # webrtcvad.Vad instance
_vox_stt_model = None  # the loaded STT model
_vox_stt_processor = None  # tokenizer / feature extractor
_vox_stt_load_lock = _vox_threading_stt.Lock()
_vox_stt_load_failed = False
_vox_stt_load_triggered = False  # ensures we only kick the load thread once

# Per-utterance state (single-call POC — no per-session demux)
_vox_audio_buffer = []  # list[np.float32 array, 24kHz] currently accumulating
_vox_vad_carryover = _vox_np.zeros(0, dtype=_vox_np.float32)  # 24k samples held for VAD frame alignment
_vox_in_speech = False
_vox_silence_run_ms = 0
_vox_speech_run_ms = 0
_vox_buffer_lock = _vox_threading_stt.Lock()

# Background work queue — STT inference runs on a dedicated thread so the
# audio path never blocks on model inference.
_vox_stt_jobs: "_vox_queue.Queue" = _vox_queue.Queue(maxsize=8)
_vox_stt_worker_started = False


def _vox_stt_log(msg, *args):
    # Use moshi's stderr — we don't have access to its logger from here, and
    # we never want to print() and interfere with whatever stdout protocol
    # the server uses.
    import sys
    sys.stderr.write("[voxreach-stt] " + (msg % args if args else msg) + "\n")
    sys.stderr.flush()


def _vox_stt_lazy_load():
    """Load Kyutai STT once; subsequent calls are no-ops."""
    global _vox_stt_vad, _vox_stt_model, _vox_stt_processor, _vox_stt_load_failed
    if _vox_stt_model is not None or _vox_stt_load_failed:
        return _vox_stt_model is not None
    with _vox_stt_load_lock:
        if _vox_stt_model is not None or _vox_stt_load_failed:
            return _vox_stt_model is not None
        try:
            import webrtcvad  # noqa: F401
            _vox_stt_vad = webrtcvad.Vad(_VOX_VAD_AGGRESSIVENESS)
        except Exception as e:
            _vox_stt_log("webrtcvad import/init failed: %s — STT disabled", e)
            _vox_stt_load_failed = True
            return False
        try:
            # Kyutai STT models ship with the moshi/STT Python API. We import
            # lazily so a bad install doesn't crash moshi on boot.
            from transformers import AutoModelForSpeechSeq2Seq, AutoProcessor
            import torch as _torch
            _vox_stt_log("loading STT model %s ...", _VOX_STT_MODEL_ID)
            t0 = _vox_time_stt.time()
            _vox_stt_processor = AutoProcessor.from_pretrained(_VOX_STT_MODEL_ID)
            _vox_stt_model = AutoModelForSpeechSeq2Seq.from_pretrained(
                _VOX_STT_MODEL_ID,
                torch_dtype=_torch.float16,
                device_map="cuda" if _torch.cuda.is_available() else "cpu",
            )
            _vox_stt_log("STT loaded in %.1fs", _vox_time_stt.time() - t0)
        except Exception as e:
            _vox_stt_log("STT model load failed: %s — STT disabled", e)
            _vox_stt_load_failed = True
            return False
    return True


def _vox_stt_worker():
    """Drain the job queue, run STT, POST results to the sidecar."""
    import torch as _torch
    while True:
        job = _vox_stt_jobs.get()
        if job is None:  # poison pill on shutdown
            return
        pcm_24k = job
        try:
            t0 = _vox_time_stt.time()
            # Resample 24k → 16k for STT (Kyutai expects 16k)
            ratio = 16000.0 / _VOX_INPUT_SAMPLE_RATE
            tgt_len = int(round(len(pcm_24k) * ratio))
            if tgt_len <= 0:
                continue
            idx = _vox_np.linspace(0, len(pcm_24k) - 1, tgt_len).astype(_vox_np.int64)
            pcm_16k = pcm_24k[idx]
            inputs = _vox_stt_processor(
                pcm_16k, sampling_rate=16000, return_tensors="pt"
            )
            inputs = {k: v.to(_vox_stt_model.device, dtype=_torch.float16 if v.dtype.is_floating_point else v.dtype)
                      for k, v in inputs.items()}
            with _torch.inference_mode():
                gen = _vox_stt_model.generate(**inputs, max_new_tokens=200)
            text = _vox_stt_processor.batch_decode(gen, skip_special_tokens=True)[0].strip()
            elapsed_ms = int((_vox_time_stt.time() - t0) * 1000)
            if text:
                _vox_stt_log("customer (%dms): %s", elapsed_ms, text)
                _voxreach_post("/api/transcript", {"role": "customer", "text": text, "latency_ms": elapsed_ms})
        except Exception as e:
            _vox_stt_log("STT inference failed: %s", e)
        finally:
            _vox_stt_jobs.task_done()


def _vox_stt_ensure_worker():
    global _vox_stt_worker_started
    if _vox_stt_worker_started:
        return
    _vox_stt_worker_started = True
    t = _vox_threading_stt.Thread(target=_vox_stt_worker, daemon=True, name="voxreach-stt-worker")
    t.start()


def _vox_resample_24k_to_16k(pcm_24k):
    """Cheap linear-interp resample, good enough for VAD."""
    if len(pcm_24k) == 0:
        return _vox_np.zeros(0, dtype=_vox_np.int16)
    ratio = _VOX_VAD_SAMPLE_RATE / _VOX_INPUT_SAMPLE_RATE
    tgt_len = int(round(len(pcm_24k) * ratio))
    if tgt_len == 0:
        return _vox_np.zeros(0, dtype=_vox_np.int16)
    idx = _vox_np.linspace(0, len(pcm_24k) - 1, tgt_len).astype(_vox_np.int64)
    out = pcm_24k[idx]
    return (_vox_np.clip(out, -1.0, 1.0) * 32767.0).astype(_vox_np.int16)


def _vox_commit_utterance_locked():
    """Caller holds _vox_buffer_lock. Pulls accumulated audio, queues STT."""
    global _vox_audio_buffer
    if not _vox_audio_buffer:
        return
    full = _vox_np.concatenate(_vox_audio_buffer)
    _vox_audio_buffer = []
    duration_ms = int(len(full) * 1000 / _VOX_INPUT_SAMPLE_RATE)
    if duration_ms < _VOX_VAD_MIN_UTT_MS:
        return  # noise / "uh-huh" filter
    try:
        _vox_stt_jobs.put_nowait(full)
    except _vox_queue.Full:
        _vox_stt_log("STT queue full — dropping %dms utterance", duration_ms)


def _vox_stt_kick_load_async():
    """Non-blocking. Starts model load in a background thread if needed."""
    global _vox_stt_load_triggered
    if _vox_stt_model is not None or _vox_stt_load_failed or _vox_stt_load_triggered:
        return
    _vox_stt_load_triggered = True
    _vox_threading_stt.Thread(
        target=_vox_stt_lazy_load, daemon=True, name="voxreach-stt-loader",
    ).start()


def voxreach_customer_audio(pcm_chunk):
    """Hook called from moshi.server with each decoded customer-audio chunk.

    pcm_chunk: numpy float32 array OR torch.Tensor, mono, 24 kHz, in [-1, 1].
    Any shape that can be flattened to 1-D works.

    Non-blocking: while the STT model is still loading (first ~3-5s of a
    call), this drops frames silently. The audio path never waits.
    """
    if not _VOX_STT_ENABLED:
        return
    if _vox_stt_model is None:
        _vox_stt_kick_load_async()
        return  # drop frame; model not ready yet
    _vox_stt_ensure_worker()
    # Normalize to numpy float32 1-D
    try:
        if hasattr(pcm_chunk, "detach"):  # torch tensor
            arr = pcm_chunk.detach().to("cpu").to(dtype=__import__("torch").float32).numpy().reshape(-1)
        else:
            arr = _vox_np.asarray(pcm_chunk, dtype=_vox_np.float32).reshape(-1)
    except Exception as e:
        _vox_stt_log("could not normalize pcm chunk: %s", e)
        return
    if arr.size == 0:
        return

    global _vox_vad_carryover, _vox_in_speech, _vox_silence_run_ms, _vox_speech_run_ms

    with _vox_buffer_lock:
        # Always accumulate audio for STT (always 24 kHz path)
        _vox_audio_buffer.append(arr)

        # VAD path — concatenate to carryover, walk in 20ms frames at 16 kHz
        pcm_16k = _vox_resample_24k_to_16k(arr)
        if _vox_vad_carryover.size:
            pcm_16k = _vox_np.concatenate([_vox_vad_carryover, pcm_16k])
        n_frames = len(pcm_16k) // _VOX_VAD_FRAME_LEN
        if n_frames == 0:
            _vox_vad_carryover = pcm_16k
            return
        usable = n_frames * _VOX_VAD_FRAME_LEN
        _vox_vad_carryover = pcm_16k[usable:].copy()
        frames = pcm_16k[:usable].reshape(n_frames, _VOX_VAD_FRAME_LEN)

        for frame in frames:
            is_speech = False
            try:
                is_speech = _vox_stt_vad.is_speech(frame.tobytes(), _VOX_VAD_SAMPLE_RATE)
            except Exception:
                pass
            if is_speech:
                _vox_in_speech = True
                _vox_silence_run_ms = 0
                _vox_speech_run_ms += _VOX_VAD_FRAME_MS
                # Hard cap on a single utterance — flush even mid-speech
                if _vox_speech_run_ms >= _VOX_VAD_MAX_UTT_MS:
                    _vox_commit_utterance_locked()
                    _vox_in_speech = False
                    _vox_speech_run_ms = 0
                    _vox_silence_run_ms = 0
            else:
                if _vox_in_speech:
                    _vox_silence_run_ms += _VOX_VAD_FRAME_MS
                    if _vox_silence_run_ms >= _VOX_VAD_SILENCE_MS:
                        _vox_commit_utterance_locked()
                        _vox_in_speech = False
                        _vox_speech_run_ms = 0
                        _vox_silence_run_ms = 0
                else:
                    # Trim trailing buffer when we're still in silence to bound memory
                    if len(_vox_audio_buffer) > 1:
                        # Keep just the most recent chunk so we have prefix context
                        _vox_audio_buffer = _vox_audio_buffer[-1:]


def voxreach_customer_stt_flush(timeout_s=5.0):
    """Called at call_end — commit whatever's still buffered AND wait for the
    STT worker to drain, so the customer's last spoken words land at the
    sidecar before /api/call/end triggers the forced final extraction.

    Hard-bounded at timeout_s seconds. If STT inference is genuinely stuck
    (model error, GPU hang), we'd rather end the call than freeze indefinitely.
    """
    global _vox_in_speech, _vox_silence_run_ms, _vox_speech_run_ms, _vox_vad_carryover, _vox_audio_buffer
    with _vox_buffer_lock:
        _vox_commit_utterance_locked()
        _vox_in_speech = False
        _vox_silence_run_ms = 0
        _vox_speech_run_ms = 0
        _vox_vad_carryover = _vox_np.zeros(0, dtype=_vox_np.float32)
        _vox_audio_buffer = []
    # Wait for in-flight jobs to complete (poll; Queue.join() has no timeout)
    deadline = _vox_time_stt.time() + timeout_s
    while _vox_time_stt.time() < deadline:
        if _vox_stt_jobs.unfinished_tasks == 0:
            return
        _vox_time_stt.sleep(0.05)
    _vox_stt_log("flush timed out with %d STT jobs still pending", _vox_stt_jobs.unfinished_tasks)

# end VoxReach customer STT bridge v1
'''
