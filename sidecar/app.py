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

from mock_transcript import play_mock_call
from order_extractor import get_extractor
from pos_stub import write_order
from schema import LatencyMetric, RetrievalHit, SSEEvent, TranscriptTurn
from state import store

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
log = logging.getLogger("voxreach.sidecar")


@asynccontextmanager
async def lifespan(_: FastAPI):
    log.info("voxreach sidecar starting up")
    # Warm up the extractor (this picks LLM-vs-rule based on Ollama reachability)
    extractor = get_extractor()
    log.info("extractor warmed: %s", type(extractor).__name__)
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
    state = await store.start_call()
    return {"call_id": state.call_id}


@app.post("/api/call/end")
async def call_end():
    state = await store.end_call()
    if state is None:
        raise HTTPException(404, "no active call")

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


@app.post("/api/transcript")
async def transcript(turn: TranscriptIn):
    """Ingest a new transcript turn, run order extraction, emit granular events.

    The extractor sees the full rolling transcript every turn and returns the
    complete current order state. state.apply_extraction() diffs against the
    previous state and emits item_added / item_removed / item_modified events
    accordingly — so cancellations and quantity changes flow to the UI naturally.
    """
    state = store.current
    if state is None or state.status != "connected":
        raise HTTPException(409, "no active call")
    if turn.role not in ("customer", "vox"):
        raise HTTPException(422, "role must be 'customer' or 'vox'")

    t = TranscriptTurn(role=turn.role, text=turn.text, latency_ms=turn.latency_ms)
    state.transcript.append(t)
    await store.publish(SSEEvent(event="transcript_turn", data=t.model_dump(mode="json")))

    # Run the extractor against the full conversation so far
    extractor = get_extractor()
    t0 = time.perf_counter()
    extraction = await asyncio.to_thread(extractor.extract, list(state.transcript))
    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    state.latency.extraction_latency_ms = elapsed_ms

    await store.apply_extraction(extraction)

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


@app.get("/api/events")
async def events():
    queue = store.subscribe()

    async def gen():
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

    return EventSourceResponse(gen())


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
