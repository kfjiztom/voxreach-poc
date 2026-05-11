"""Canned conversations for local demo development without a GPU.

Each scenario is a sequence of (delay_ms, kind, payload) tuples. Kinds:
  - "transcript": payload = (role, text, latency_ms_or_None)
  - "retrieval":  payload = (path, snippet, score)

Mock playback now routes each transcript turn through the same order
extraction pipeline that real audio uses (order_extractor.get_extractor()),
so cancel/modify scenarios faithfully simulate the production flow.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from order_extractor import get_extractor
from schema import RetrievalHit, SSEEvent, TranscriptTurn
from state import store

log = logging.getLogger("voxreach.mock")


SCENARIOS: dict[str, list[tuple[int, str, Any]]] = {
    "order": [
        (300, "transcript", ("vox", "Thanks for calling Hearth and Pass — this is Vox. How can I help you today?", 280)),
        (1800, "transcript", ("customer", "Hi, can I place a takeout order?", None)),
        (600, "transcript", ("vox", "Of course. What can I get started for you?", 310)),
        (1400, "transcript", ("customer", "I'd like one bulgogi and one haemul pajeon.", None)),
        (50, "retrieval", ("menu.mains.bulgogi", "Bulgogi $19 — soy-pear marinated rib-eye, lettuce wraps, jasmine rice. DF, mild.", 0.94)),
        (50, "retrieval", ("menu.banchan.haemul_pajeon", "Haemul Pajeon $13 — crisp scallion-and-seafood pancake, soy-vinegar dipping sauce. Chef's pick.", 0.91)),
        (700, "transcript", ("vox", "One bulgogi at nineteen, one haemul pajeon at thirteen. That's thirty-two before tax. Anything else?", 380)),
        (1500, "transcript", ("customer", "No, that's it. Pickup at six thirty please.", None)),
        (500, "transcript", ("vox", "Six thirty pickup — got it. Who's the order under, and what's a good number?", 320)),
        (1800, "transcript", ("customer", "It's under Maya. My number is 515-555-0182.", None)),
        (650, "transcript", ("vox", "Maya, five-one-five five-five-five oh-one-eight-two. Let me confirm: one bulgogi, one haemul pajeon, thirty-two dollars before tax, ready at six thirty. We'll see you then.", 410)),
        (1200, "transcript", ("customer", "Perfect, thanks!", None)),
        (500, "transcript", ("vox", "Thanks Maya — see you at six thirty. Bye.", 290)),
    ],
    "info": [
        (300, "transcript", ("vox", "Thanks for calling Hearth and Pass — this is Vox. How can I help you today?", 285)),
        (1800, "transcript", ("customer", "Are you open today?", None)),
        (50, "retrieval", ("hours", "Tuesday-Thursday 11am-9pm; Friday-Saturday 11am-10pm; Sunday 11am-9pm; Monday closed.", 0.96)),
        (550, "transcript", ("vox", "Yes, we're open today until nine. Friday and Saturday we're open till ten.", 295)),
        (1300, "transcript", ("customer", "Do you have vegan options?", None)),
        (50, "retrieval", ("policies.dietary", "Korean food does dietary range better than most cuisines. Every dish flagged for vegan, GF, DF, heat. Vegan: kimchi trio, japchae with tofu, bibimbap with tofu, all teas.", 0.93)),
        (700, "transcript", ("vox", "Yes — the kimchi trio and all our teas are vegan, and we can do japchae or bibimbap with tofu instead of beef.", 340)),
        (1100, "transcript", ("customer", "Great. Where do you park?", None)),
        (50, "retrieval", ("policies.parking", "Two-hour metered street parking on Locust. Free public ramp on 4th and Walnut after 5pm and weekends.", 0.88)),
        (550, "transcript", ("vox", "Two-hour metered parking on Locust right out front, or the public ramp on Fourth and Walnut is free after five and on weekends.", 360)),
        (1000, "transcript", ("customer", "Perfect, thanks!", None)),
        (450, "transcript", ("vox", "You're welcome — see you soon.", 280)),
    ],
    "escalate": [
        (300, "transcript", ("vox", "Thanks for calling Hearth and Pass — this is Vox. How can I help you today?", 280)),
        (1500, "transcript", ("customer", "Yeah, I had a really bad experience here last night. Can I speak to the manager?", None)),
        (700, "transcript", ("vox", "I'm sorry to hear that. Let me get a team member on the line for you — one moment please.", 320)),
    ],
    # NEW: shows the cancel/modify path — the real moat moment for VoxReach
    "modify": [
        (300, "transcript", ("vox", "Thanks for calling Hearth and Pass — this is Vox. How can I help you today?", 280)),
        (1500, "transcript", ("customer", "Can I order one bulgogi for pickup?", None)),
        (50, "retrieval", ("menu.mains.bulgogi", "Bulgogi $19 — chef's pick. Soy-pear marinated rib-eye.", 0.95)),
        (600, "transcript", ("vox", "One bulgogi at nineteen dollars. Anything else?", 320)),
        (1700, "transcript", ("customer", "Actually, scratch the bulgogi. Make it two haemul pajeon instead.", None)),
        (50, "retrieval", ("menu.banchan.haemul_pajeon", "Haemul Pajeon $13 — chef's pick.", 0.92)),
        (700, "transcript", ("vox", "Got it — taking the bulgogi off, two haemul pajeon at thirteen each. That's twenty-six dollars before tax.", 350)),
        (1500, "transcript", ("customer", "Perfect. And actually, can I get a kimchi jjigae too?", None)),
        (50, "retrieval", ("menu.mains.kimchi_jjigae", "Kimchi Jjigae $17 — hot.", 0.93)),
        (650, "transcript", ("vox", "Sure — adding kimchi jjigae at seventeen. So that's two pajeon and one kimchi jjigae, forty-three dollars before tax.", 360)),
        (1300, "transcript", ("customer", "Six PM pickup, under Sam, 515-555-0199.", None)),
        (700, "transcript", ("vox", "Six PM, under Sam, five-one-five five-five-five oh-one-nine-nine. Confirming: two haemul pajeon, one kimchi jjigae, forty-three before tax, ready at six. See you then.", 400)),
        (1100, "transcript", ("customer", "Thanks!", None)),
        (450, "transcript", ("vox", "Thanks Sam — see you at six.", 280)),
    ],
}


async def play_mock_call(scenario: str) -> None:
    if scenario not in SCENARIOS:
        log.warning("unknown scenario: %s", scenario)
        return

    state = await store.start_call()
    extractor = get_extractor()
    log.info("mock call started call_id=%s scenario=%s extractor=%s",
             state.call_id, scenario, type(extractor).__name__)

    for delay_ms, kind, payload in SCENARIOS[scenario]:
        await asyncio.sleep(delay_ms / 1000)
        live = store.current
        if live is None or live.status != "connected":
            log.info("mock call interrupted, stopping scenario")
            return

        if kind == "transcript":
            role, text, latency = payload
            turn = TranscriptTurn(role=role, text=text, latency_ms=latency)
            live.transcript.append(turn)
            await store.publish(SSEEvent(event="transcript_turn", data=turn.model_dump(mode="json")))

            # Run extraction on the full rolling transcript and apply diff events
            t0 = time.perf_counter()
            extraction = await asyncio.to_thread(extractor.extract, list(live.transcript))
            elapsed_ms = int((time.perf_counter() - t0) * 1000)
            live.latency.extraction_latency_ms = elapsed_ms
            await store.apply_extraction(extraction)

            if latency is not None:
                if live.latency.first_audio_ms is None:
                    live.latency.first_audio_ms = latency
                live.latency.last_turn_ms = latency
                prior = live.latency.avg_turn_ms or latency
                live.latency.avg_turn_ms = int((prior + latency) / 2)
            await store.publish(SSEEvent(event="latency_updated", data=live.latency.model_dump()))

        elif kind == "retrieval":
            path, snippet, score = payload
            hit = RetrievalHit(path=path, snippet=snippet, score=score)
            live.retrieval_log.append(hit)
            live.latency.rag_hits = len(live.retrieval_log)
            await store.publish(SSEEvent(event="retrieval_hit", data=hit.model_dump(mode="json")))
            await store.publish(SSEEvent(event="latency_updated", data=live.latency.model_dump()))

    # End the call automatically after the scenario completes
    await asyncio.sleep(0.8)
    final = await store.end_call()
    if final and final.order.active_items:
        from pos_stub import write_order
        await store.publish(SSEEvent(event="pos_write", data={"status": "writing"}))
        try:
            response = await write_order(final.order)
            final.pos_write_status = "written"
            final.order.status = "written_to_pos"
            await store.publish(
                SSEEvent(
                    event="pos_write",
                    data={"status": "written", "response": response, "order": final.order.model_dump(mode="json")},
                )
            )
        except Exception as e:
            await store.publish(SSEEvent(event="pos_write", data={"status": "failed", "error": str(e)}))
