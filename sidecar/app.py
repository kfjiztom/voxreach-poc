"""FastAPI sidecar.

Endpoints:
  POST /api/call/start        → begin a new call session, return call_id
  POST /api/call/end          → end the active call, trigger POS write
  POST /api/transcript        → ingest a transcript turn (called by Moshi server hook)
  POST /api/retrieval         → ingest a RAG retrieval hit (called by retrieval back-end)
  POST /api/latency           → ingest latency metrics (optional)
  GET  /api/state             → current snapshot (debug / first-load hydration)
  GET  /api/events            → Server-Sent Events stream for the frontend
  POST /api/mock/play         → drive a canned conversation locally (no GPU needed)
"""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from knowledge_search import get_search
from mock_transcript import play_mock_call
from order_extractor import final_order_check, get_extractor, validate_against_transcript
from pos_stub import write_order
from schema import LatencyMetric, RetrievalHit, SSEEvent, TranscriptTurn
from state import store

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
log = logging.getLogger("voxreach.sidecar")


@asynccontextmanager
async def lifespan(_: FastAPI):
    log.info("voxreach sidecar starting up")
    # Warm up the extractor (LLM vs rule, based on Ollama reachability)
    extractor = get_extractor()
    log.info("extractor warmed: %s", type(extractor).__name__)

    # Warm up RAG in the background — slow first call (~3s) but lets uvicorn
    # bind the port immediately. If sentence-transformers isn't installed,
    # warmup is a no-op and search() returns [] silently.
    async def _warm_rag():
        try:
            await asyncio.to_thread(get_search().warmup)
        except Exception as e:
            log.warning("RAG warmup failed: %s", e)
    asyncio.create_task(_warm_rag())

    yield
    log.info("voxreach sidecar shutting down")


app = FastAPI(title="VoxReach POC sidecar", version="0.2.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# -- request models ---------------------------------------------------------


class TranscriptIn(BaseModel):
    role: str
    text: str
    latency_ms: int | None = None


class RetrievalIn(BaseModel):
    path: str
    snippet: str
    score: float | None = None


# -- routes -----------------------------------------------------------------


@app.post("/api/call/start")
async def call_start():
    # Reset per-call RAG dedup set
    global _seen_rag_paths, _last_rag_at, _last_extract_at
    _seen_rag_paths = set()
    _last_rag_at = 0.0
    _last_extract_at = 0.0
    state = await store.start_call()
    return {"call_id": state.call_id}


@app.post("/api/call/end")
async def call_end():
    # Run one final extraction on the full transcript before tearing down —
    # bypasses the throttle so we always have the latest state captured.
    pre_state = store.current
    if pre_state is not None and pre_state.transcript:
        try:
            await _maybe_run_extraction(pre_state, force=True)
        except Exception as e:
            log.warning("final extraction failed: %s", e)

    state = await store.end_call()
    if state is None:
        raise HTTPException(404, "no active call")

    # Final transcript-grounded sanity check BEFORE the POS write. Walks every
    # active item on the ticket and confirms there's a customer utterance
    # supporting it. Items without evidence get marked "removed" (not deleted,
    # so they still show up in the UI as struck-through) so the kitchen never
    # receives a hallucinated order. This is the "double-check on the entire
    # conversation before it passes to Toast" guard.
    if state.transcript:
        suspect = final_order_check(state.order.items, list(state.transcript))
        if suspect:
            log.warning(
                "[final-check] dropping %d items with no transcript evidence: %s",
                len(suspect), ", ".join(suspect),
            )
            for item in state.order.items:
                if item.name in suspect and item.status != "removed":
                    item.status = "removed"
                    await store.publish(SSEEvent(event="item_removed", data={"name": item.name}))
            # Push a fresh snapshot so the UI shows the corrected order
            await store.publish(SSEEvent(
                event="order_updated",
                data={"order": state.order.model_dump(mode="json")},
            ))

    if state.order.active_items:
        await store.publish(SSEEvent(event="pos_write", data={"status": "writing"}))
        try:
            response = await write_order(state.order)
            state.pos_write_status = "written"
            state.order.status = "written_to_pos"
            await store.publish(
                SSEEvent(
                    event="pos_write",
                    data={"status": "written", "response": response, "order": state.order.model_dump(mode="json")},
                )
            )
        except Exception as e:
            state.pos_write_status = "failed"
            await store.publish(SSEEvent(event="pos_write", data={"status": "failed", "error": str(e)}))

    return state.model_dump(mode="json")


# ---------------------------------------------------------------------------
# Escalation detection
# ---------------------------------------------------------------------------
# Phrases the persona instructs Vox to say verbatim when handing off. If any
# of these show up in Vox's transcript, we flag the call. Match is loose
# (substring, case-insensitive) to survive sentence buffering quirks.
_ESCALATION_PHRASES = (
    "get a team member",
    "team member on the line",
    "transfer you",
    "transferring you",
    "connect you with a manager",
    "let me get the manager",
    "have a manager call you back",
)
# Topic hint — last customer turn that preceded the handoff. We pattern-match
# the recent transcript to give the UI a useful reason badge.
_ESCALATION_TOPIC_KEYWORDS = {
    "allergy": ["allergy", "allergic", "peanut", "gluten", "dairy"],
    "complaint": ["complaint", "refund", "problem", "wrong order", "missing"],
    "catering": ["catering", "private event", "large party", "party of"],
    "jobs": ["job", "hiring", "apply", "employment"],
    "off-menu": ["do you have", "do you serve", "can i get"],
}


def _detect_escalation(vox_text: str, state) -> str | None:
    """Return a reason string if Vox just spoke an escalation phrase, else None."""
    lo = vox_text.lower()
    if not any(p in lo for p in _ESCALATION_PHRASES):
        return None
    # Walk back through recent customer turns to guess WHY Vox escalated.
    for t in reversed(state.transcript[-10:]):
        if t.role != "customer":
            continue
        ct = t.text.lower()
        for topic, kws in _ESCALATION_TOPIC_KEYWORDS.items():
            if any(k in ct for k in kws):
                return topic
        break  # only look at the most recent customer turn
    return "general"


# Throttle state for in-call extraction.
# Moshi's transcript bridge POSTs every model text token (10-15/sec). Running
# Gemma extraction on every POST overwhelms Ollama's queue — most calls then
# time out, latency balloons, and we get hundreds of "LLM extraction failed"
# log entries during a single call.
#
# Strategy: do extraction at most once every EXTRACTION_THROTTLE_SEC seconds
# during the call, AND one guaranteed final extraction on call_end. The user
# can override via VOXREACH_EXTRACT_THROTTLE_SEC env.
import os as _os
EXTRACTION_THROTTLE_SEC = float(_os.environ.get("VOXREACH_EXTRACT_THROTTLE_SEC", "3.0"))
_last_extract_at: float = 0.0
_extract_lock = asyncio.Lock()

# RAG search runs at a faster cadence — it's CPU-only, ~10ms per query, so
# we can afford to refresh the panel often. But still throttle to avoid
# spamming SSE events during a token storm.
RAG_THROTTLE_SEC = float(_os.environ.get("VOXREACH_RAG_THROTTLE_SEC", "1.0"))
_last_rag_at: float = 0.0
# Track which paths we've already surfaced this call so the panel doesn't
# fill with duplicates of the same item.
_seen_rag_paths: set[str] = set()


async def _maybe_run_extraction(state, *, force: bool = False) -> int | None:
    """Run extraction if at least EXTRACTION_THROTTLE_SEC has passed (or force=True).

    Returns extraction elapsed_ms when run, None when throttled.
    Uses a lock so only one extraction is in-flight at a time per call.
    """
    global _last_extract_at
    now = time.monotonic()
    if not force and (now - _last_extract_at) < EXTRACTION_THROTTLE_SEC:
        return None
    if _extract_lock.locked():
        # An extraction is already running — skip this turn rather than queue
        return None

    async with _extract_lock:
        _last_extract_at = time.monotonic()
        extractor = get_extractor()
        t0 = time.perf_counter()
        extraction = await asyncio.to_thread(extractor.extract, list(state.transcript))
        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        state.latency.extraction_latency_ms = elapsed_ms

        # Transcript-grounded validation — defense against the small-model
        # hallucination where the extractor "adds" items the customer never
        # actually mentioned (most commonly bulgogi, since it's the first
        # item in the menu and in the system prompt examples).
        cleaned, warnings = validate_against_transcript(extraction, list(state.transcript))
        for w in warnings:
            log.warning("[extract-validate] %s", w)

        await store.apply_extraction(cleaned)
        return elapsed_ms


async def _maybe_run_rag(state, query: str) -> None:
    """Throttled semantic search over the knowledge pack. Emits retrieval_hit
    events for each new menu/policy/FAQ match so the right pane fills as the
    conversation references things.

    Only fires once per RAG_THROTTLE_SEC. Skips paths we've already surfaced
    this call to avoid duplicate entries in the UI.
    """
    global _last_rag_at
    now = time.monotonic()
    if (now - _last_rag_at) < RAG_THROTTLE_SEC:
        return
    _last_rag_at = now

    search = get_search()
    if not search.ready:
        return
    try:
        hits = await asyncio.to_thread(search.search, query)
    except Exception as e:
        log.debug("RAG search failed: %s", e)
        return

    for hit in hits:
        path = hit["path"]
        if path in _seen_rag_paths:
            continue
        _seen_rag_paths.add(path)
        h = RetrievalHit(path=path, snippet=hit["snippet"], score=hit.get("score"))
        state.retrieval_log.append(h)
        state.latency.rag_hits = len(state.retrieval_log)
        await store.publish(SSEEvent(event="retrieval_hit", data=h.model_dump(mode="json")))


@app.post("/api/transcript")
async def transcript(turn: TranscriptIn):
    """Ingest a new transcript turn, run THROTTLED extraction, emit events.

    Moshi's bridge POSTs every text token. We accept all of them into the
    transcript history but only run the LLM extractor at most once every
    EXTRACTION_THROTTLE_SEC seconds — see _maybe_run_extraction docstring.
    """
    state = store.current
    if state is None or state.status != "connected":
        raise HTTPException(409, "no active call")
    if turn.role not in ("customer", "vox"):
        raise HTTPException(422, "role must be 'customer' or 'vox'")

    t = TranscriptTurn(role=turn.role, text=turn.text, latency_ms=turn.latency_ms)
    state.transcript.append(t)
    await store.publish(SSEEvent(event="transcript_turn", data=t.model_dump(mode="json")))

    # Escalation detection — only on Vox's side. If Vox uses an escalation
    # phrase from the persona ("Let me get a team member..."), flag the call
    # as needing human handoff.
    if turn.role == "vox":
        reason = _detect_escalation(turn.text, state)
        if reason and not state.needs_human:
            state.needs_human = True
            state.escalation_reason = reason
            log.info("escalation triggered (reason=%s) on call_id=%s", reason, state.call_id)
            await store.publish(SSEEvent(
                event="escalate",
                data={"reason": reason, "triggered_by": turn.text[:200]},
            ))

    # RAG — fast, CPU-only, fills the right-pane "Knowledge Retrieved" panel
    await _maybe_run_rag(state, turn.text)

    # LLM-based order extraction — slow, throttled to once per 3s
    elapsed_ms = await _maybe_run_extraction(state)

    # Latency bookkeeping
    if turn.latency_ms is not None:
        if state.latency.first_audio_ms is None:
            state.latency.first_audio_ms = turn.latency_ms
        state.latency.last_turn_ms = turn.latency_ms
        prior_avg = state.latency.avg_turn_ms or turn.latency_ms
        state.latency.avg_turn_ms = int((prior_avg + turn.latency_ms) / 2)
    await store.publish(SSEEvent(event="latency_updated", data=state.latency.model_dump()))

    return {
        "ok": True,
        "items_in_order": len(state.order.active_items),
        "extracted": elapsed_ms is not None,
        "extraction_ms": elapsed_ms,
    }


@app.post("/api/retrieval")
async def retrieval(hit: RetrievalIn):
    state = store.current
    if state is None:
        raise HTTPException(409, "no active call")

    h = RetrievalHit(path=hit.path, snippet=hit.snippet, score=hit.score)
    state.retrieval_log.append(h)
    state.latency.rag_hits = len(state.retrieval_log)
    await store.publish(SSEEvent(event="retrieval_hit", data=h.model_dump(mode="json")))
    await store.publish(SSEEvent(event="latency_updated", data=state.latency.model_dump()))
    return {"ok": True}


@app.post("/api/latency")
async def latency(metric: LatencyMetric):
    state = store.current
    if state is None:
        raise HTTPException(409, "no active call")
    state.latency = metric
    await store.publish(SSEEvent(event="latency_updated", data=metric.model_dump()))
    return {"ok": True}


@app.get("/api/state")
async def get_state():
    state = store.current
    return state.model_dump(mode="json") if state else None


@app.get("/api/extractor")
async def get_extractor_info():
    """Debug — tells you whether the LLM or rule-based extractor is active."""
    extractor = get_extractor()
    return {"extractor": type(extractor).__name__}


@app.post("/api/order/readback")
async def order_readback():
    """Synthesize the live order ticket as audio so the operator (or caller,
    via the browser) can hear the kitchen-bound order spoken back.

    Allowed on BOTH live calls and just-ended calls — operators routinely
    want to re-verify after the caller hangs up. We only 404 when no call
    state has ever existed in this sidecar instance.

    Runs Piper on CPU off the main event loop — moshi's GPU is never touched.
    Returns audio/wav (mono, ~22 kHz) plus the spoken text as a header for
    debugging.
    """
    from starlette.responses import Response
    from tts import synth_readback

    state = store.current
    if state is None:
        raise HTTPException(404, "no call yet — start a call first")
    if not state.order.active_items:
        raise HTTPException(409, "order is empty — nothing to read back")

    try:
        wav_bytes, text = await asyncio.to_thread(synth_readback, state.order)
    except FileNotFoundError as e:
        raise HTTPException(503, f"Piper voice files missing: {e}")
    except Exception as e:
        log.exception("TTS readback failed")
        raise HTTPException(500, f"readback synthesis failed: {e}")

    log.info("readback synthesized (%d bytes, %d chars text)", len(wav_bytes), len(text))
    return Response(
        content=wav_bytes,
        media_type="audio/wav",
        headers={
            "X-Readback-Text": text[:512],  # for DevTools / debugging
            "Cache-Control": "no-store",
        },
    )


@app.get("/api/events")
async def events():
    queue = store.subscribe()

    async def gen():
        # Initial flush so HTTP/2 proxies (RunPod, Cloudflare) see the stream
        # immediately and don't kill the connection as malformed.
        yield {"event": "ready", "data": "{}"}
        try:
            # Hydrate new subscriber with current snapshot
            if store.current:
                yield {"event": "snapshot", "data": store.current.model_dump_json()}
            while True:
                event = await queue.get()
                yield {"event": event.event, "data": event.model_dump_json()}
        except asyncio.CancelledError:
            pass
        finally:
            store.unsubscribe(queue)

    # ping=15 sends a `: ping\n\n` comment every 15s. This is what keeps the
    # connection alive across HTTP/2 proxies; without it RunPod/Cloudflare
    # treat the silent stream as a protocol violation and return
    # ERR_HTTP2_PROTOCOL_ERROR to the browser within ~30s.
    return EventSourceResponse(
        gen(),
        ping=15,
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",  # disable nginx buffering, just in case
            "Connection": "keep-alive",
        },
    )


@app.post("/api/mock/play")
async def mock_play(scenario: str = "order"):
    """Drive a canned conversation through the sidecar — no GPU needed.

    Scenarios:
      - "order"      : caller orders bulgogi + pajeon, name + pickup time
      - "info"       : caller asks hours, vegan options, parking
      - "escalate"   : caller asks for the manager, triggers transfer
      - "modify"     : caller adds bulgogi, then changes their mind to two pajeon
    """
    if store.current and store.current.status == "connected":
        raise HTTPException(409, "a call is already active")
    asyncio.create_task(play_mock_call(scenario))
    return {"ok": True, "scenario": scenario}
