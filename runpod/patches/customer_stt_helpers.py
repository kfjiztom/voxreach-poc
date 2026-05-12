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

v2 — switched from Kyutai STT (no transformers loader; model_type "stt" is
unrecognized by transformers v4.x and adding the kyutai inference repo
caused a torch/numpy/safetensors dep cascade against moshi-personaplex)
to faster-whisper distil-large-v3. The VAD + buffering logic is unchanged.

The injected module assumes:
  - moshi feeds us PCM at 24 kHz, mono, float32 in [-1, 1].
  - The VoxReach transcript-bridge helpers (_voxreach_post etc.) are also
    present in the same module — we depend on _voxreach_post() to ship
    customer transcripts to the sidecar.
"""

from __future__ import annotations

# The exact text written into moshi.server below the existing bridge helpers.
# Marker on the first line so the patcher can detect idempotency.
HELPERS_BLOCK = r'''

# VoxReach customer STT bridge v2 (faster-whisper backend)
import numpy as _vox_np
import os as _vox_os_stt
import queue as _vox_queue
import threading as _vox_threading_stt
import time as _vox_time_stt

_VOX_STT_ENABLED = _vox_os_stt.environ.get("VOXREACH_CUSTOMER_STT_ENABLED", "1") == "1"
# faster-whisper model id. distil-large-v3 is fast (~250-500ms for 3s audio
# on a recent GPU) and well-tested for English. The Systran-converted CT2
# variant skips the transformers conversion step entirely.
_VOX_STT_MODEL_ID = _vox_os_stt.environ.get(
    "VOXREACH_STT_MODEL", "Systran/faster-distil-whisper-large-v3",
)
_VOX_STT_COMPUTE_TYPE = _vox_os_stt.environ.get("VOXREACH_STT_COMPUTE_TYPE", "float16")
_VOX_VAD_AGGRESSIVENESS = int(_vox_os_stt.environ.get("VOXREACH_VAD_AGGRESSIVENESS", "2"))
_VOX_VAD_SILENCE_MS = int(_vox_os_stt.environ.get("VOXREACH_VAD_SILENCE_MS", "500"))
_VOX_VAD_MIN_UTT_MS = int(_vox_os_stt.environ.get("VOXREACH_VAD_MIN_UTT_MS", "300"))
_VOX_VAD_MAX_UTT_MS = int(_vox_os_stt.environ.get("VOXREACH_VAD_MAX_UTT_MS", "15000"))
_VOX_INPUT_SAMPLE_RATE = int(_vox_os_stt.environ.get("VOXREACH_MOSHI_INPUT_RATE", "24000"))

# webrtcvad wants 8/16/32/48 kHz. We resample to 16 kHz for VAD AND for
# Whisper, which is natively 16 kHz mono. So one resample serves both.
_VOX_VAD_SAMPLE_RATE = 16000
_VOX_VAD_FRAME_MS = 20
_VOX_VAD_FRAME_LEN = _VOX_VAD_SAMPLE_RATE * _VOX_VAD_FRAME_MS // 1000  # 320 samples

# Lazy globals — set on first use, never re-initialized.
_vox_stt_vad = None
_vox_stt_model = None
_vox_stt_load_lock = _vox_threading_stt.Lock()
_vox_stt_load_failed = False
_vox_stt_load_triggered = False

# Per-utterance state (single-call POC — no per-session demux)
_vox_audio_buffer_16k = []  # accumulated 16 kHz float32 chunks
_vox_vad_carryover = _vox_np.zeros(0, dtype=_vox_np.float32)
_vox_in_speech = False
_vox_silence_run_ms = 0
_vox_speech_run_ms = 0
_vox_buffer_lock = _vox_threading_stt.Lock()

_vox_stt_jobs: "_vox_queue.Queue" = _vox_queue.Queue(maxsize=8)
_vox_stt_worker_started = False


def _vox_stt_log(msg, *args):
    import sys
    sys.stderr.write("[voxreach-stt] " + (msg % args if args else msg) + "\n")
    sys.stderr.flush()


def _vox_resample_24k_to_16k_f32(pcm_24k_f32):
    """Cheap linear-interp resample. Returns float32 16k array."""
    if len(pcm_24k_f32) == 0:
        return _vox_np.zeros(0, dtype=_vox_np.float32)
    ratio = _VOX_VAD_SAMPLE_RATE / _VOX_INPUT_SAMPLE_RATE
    tgt_len = int(round(len(pcm_24k_f32) * ratio))
    if tgt_len == 0:
        return _vox_np.zeros(0, dtype=_vox_np.float32)
    idx = _vox_np.linspace(0, len(pcm_24k_f32) - 1, tgt_len).astype(_vox_np.int64)
    return pcm_24k_f32[idx].astype(_vox_np.float32)


def _vox_stt_lazy_load():
    """Load faster-whisper once; subsequent calls are no-ops."""
    global _vox_stt_vad, _vox_stt_model, _vox_stt_load_failed
    if _vox_stt_model is not None or _vox_stt_load_failed:
        return _vox_stt_model is not None
    with _vox_stt_load_lock:
        if _vox_stt_model is not None or _vox_stt_load_failed:
            return _vox_stt_model is not None
        try:
            import webrtcvad
            _vox_stt_vad = webrtcvad.Vad(_VOX_VAD_AGGRESSIVENESS)
        except Exception as e:
            _vox_stt_log("webrtcvad init failed: %s — STT disabled", e)
            _vox_stt_load_failed = True
            return False
        try:
            from faster_whisper import WhisperModel
            _vox_stt_log("loading faster-whisper %s ...", _VOX_STT_MODEL_ID)
            t0 = _vox_time_stt.time()
            _vox_stt_model = WhisperModel(
                _VOX_STT_MODEL_ID,
                device="cuda",
                compute_type=_VOX_STT_COMPUTE_TYPE,
            )
            _vox_stt_log("faster-whisper loaded in %.1fs", _vox_time_stt.time() - t0)
        except Exception as e:
            _vox_stt_log("faster-whisper load failed: %s — STT disabled", e)
            _vox_stt_load_failed = True
            return False
    return True


def _vox_stt_kick_load_async():
    """Non-blocking. Starts model load in a background thread if needed."""
    global _vox_stt_load_triggered
    if _vox_stt_model is not None or _vox_stt_load_failed or _vox_stt_load_triggered:
        return
    _vox_stt_load_triggered = True
    _vox_threading_stt.Thread(
        target=_vox_stt_lazy_load, daemon=True, name="voxreach-stt-loader",
    ).start()


def _vox_stt_worker():
    """Drain the job queue, run faster-whisper, POST results to the sidecar."""
    while True:
        job = _vox_stt_jobs.get()
        if job is None:
            return
        pcm_16k = job
        try:
            t0 = _vox_time_stt.time()
            segments, info = _vox_stt_model.transcribe(
                pcm_16k,
                language="en",
                beam_size=1,
                vad_filter=False,         # we already did VAD in our pipeline
                condition_on_previous_text=False,
                no_speech_threshold=0.5,
            )
            text = " ".join(seg.text for seg in segments).strip()
            elapsed_ms = int((_vox_time_stt.time() - t0) * 1000)
            if text:
                _vox_stt_log("customer (%dms): %s", elapsed_ms, text)
                _voxreach_post(
                    "/api/transcript",
                    {"role": "customer", "text": text, "latency_ms": elapsed_ms},
                )
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


def _vox_commit_utterance_locked():
    """Caller holds _vox_buffer_lock. Pulls accumulated audio, queues STT."""
    global _vox_audio_buffer_16k
    if not _vox_audio_buffer_16k:
        return
    full = _vox_np.concatenate(_vox_audio_buffer_16k)
    _vox_audio_buffer_16k = []
    duration_ms = int(len(full) * 1000 / _VOX_VAD_SAMPLE_RATE)
    if duration_ms < _VOX_VAD_MIN_UTT_MS:
        return  # noise filter
    try:
        _vox_stt_jobs.put_nowait(full)
    except _vox_queue.Full:
        _vox_stt_log("STT queue full — dropping %dms utterance", duration_ms)


def voxreach_customer_audio(pcm_chunk):
    """Hook called from moshi.server with each decoded customer-audio chunk.

    pcm_chunk: numpy float32 array OR torch.Tensor, mono, 24 kHz, in [-1, 1].
    Non-blocking: if model isn't loaded yet, kicks an async load and drops
    the frame. Frames after load completes flow normally.
    """
    if not _VOX_STT_ENABLED:
        return
    if _vox_stt_model is None:
        _vox_stt_kick_load_async()
        return
    _vox_stt_ensure_worker()
    try:
        if hasattr(pcm_chunk, "detach"):  # torch tensor
            import torch as _torch
            arr = pcm_chunk.detach().to("cpu").to(_torch.float32).numpy().reshape(-1)
        else:
            arr = _vox_np.asarray(pcm_chunk, dtype=_vox_np.float32).reshape(-1)
    except Exception as e:
        _vox_stt_log("could not normalize pcm chunk: %s", e)
        return
    if arr.size == 0:
        return

    # One resample serves both VAD (needs 16 kHz int16) and Whisper input.
    pcm_16k_f32 = _vox_resample_24k_to_16k_f32(arr)
    if pcm_16k_f32.size == 0:
        return

    global _vox_vad_carryover, _vox_in_speech, _vox_silence_run_ms, _vox_speech_run_ms

    with _vox_buffer_lock:
        _vox_audio_buffer_16k.append(pcm_16k_f32)

        # VAD path: needs int16 frames at 16 kHz
        pcm_16k_with_carry = (
            _vox_np.concatenate([_vox_vad_carryover, pcm_16k_f32])
            if _vox_vad_carryover.size else pcm_16k_f32
        )
        n_frames = len(pcm_16k_with_carry) // _VOX_VAD_FRAME_LEN
        if n_frames == 0:
            _vox_vad_carryover = pcm_16k_with_carry
            return
        usable = n_frames * _VOX_VAD_FRAME_LEN
        _vox_vad_carryover = pcm_16k_with_carry[usable:].copy()
        frames_f32 = pcm_16k_with_carry[:usable].reshape(n_frames, _VOX_VAD_FRAME_LEN)
        frames_i16 = (_vox_np.clip(frames_f32, -1.0, 1.0) * 32767.0).astype(_vox_np.int16)

        for frame in frames_i16:
            is_speech = False
            try:
                is_speech = _vox_stt_vad.is_speech(frame.tobytes(), _VOX_VAD_SAMPLE_RATE)
            except Exception:
                pass
            if is_speech:
                _vox_in_speech = True
                _vox_silence_run_ms = 0
                _vox_speech_run_ms += _VOX_VAD_FRAME_MS
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
                    # Bound memory during silence — keep at most ~1s of prefix
                    max_prefix = _VOX_VAD_SAMPLE_RATE  # 1s at 16k
                    total = sum(len(c) for c in _vox_audio_buffer_16k)
                    while total > max_prefix and len(_vox_audio_buffer_16k) > 1:
                        total -= len(_vox_audio_buffer_16k[0])
                        _vox_audio_buffer_16k.pop(0)


def voxreach_customer_stt_flush(timeout_s=5.0):
    """Called at call_end — commit whatever's still buffered AND wait for
    in-flight STT to finish so the customer's last words land at the sidecar
    BEFORE /api/call/end triggers the forced final extraction.
    """
    global _vox_in_speech, _vox_silence_run_ms, _vox_speech_run_ms, _vox_vad_carryover, _vox_audio_buffer_16k
    with _vox_buffer_lock:
        _vox_commit_utterance_locked()
        _vox_in_speech = False
        _vox_silence_run_ms = 0
        _vox_speech_run_ms = 0
        _vox_vad_carryover = _vox_np.zeros(0, dtype=_vox_np.float32)
        _vox_audio_buffer_16k = []
    deadline = _vox_time_stt.time() + timeout_s
    while _vox_time_stt.time() < deadline:
        if _vox_stt_jobs.unfinished_tasks == 0:
            return
        _vox_time_stt.sleep(0.05)
    _vox_stt_log("flush timed out with %d STT jobs still pending", _vox_stt_jobs.unfinished_tasks)

# end VoxReach customer STT bridge v2
'''
