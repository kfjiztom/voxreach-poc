# VoxReach POC — Hearth & Pass demo

A working proof-of-concept of the VoxReach AI receptionist, built on the **Kyutai MoshiRAG** open-weights stack. The demo persona is **Hearth & Pass** (헌앤패스), a Korean restaurant in Des Moines, IA.

> **What this proves.** Full-duplex 200 ms-latency speech-to-speech, grounded in restaurant-specific knowledge via in-model RAG, with structured order extraction that mirrors a real Toast POS write — entirely on open weights, self-hosted on a single A100.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│  Browser (Chrome on RunPod's HTTPS proxy)                           │
│   ┌────────────────────┬───────────────────────────────────────┐    │
│   │ Customer view      │ Backstage view                        │    │
│   │  - Waveform        │  - Live order ticket                  │    │
│   │  - Transcript      │  - RAG retrieval log                  │    │
│   │  - Call controls   │  - Latency panel                      │    │
│   │                    │  - POS write status                   │    │
│   └────────────────────┴───────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────┘
        │ WebSocket (audio)              │ SSE (events)
        ▼                                ▼
┌──────────────────────────┐    ┌────────────────────────────────────┐
│  MoshiRAG server         │    │  FastAPI sidecar                   │
│  python -m moshi.server  │◄──►│  - transcript ingestion            │
│  (port 8998)             │    │  - rule-based intent extraction    │
│  Weights:                │    │  - POS write stub (fakes Toast)    │
│   moshika-rag-pytorch    │    │  - SSE event stream → web          │
└──────────────────────────┘    │  - mock-scenario driver (no GPU)   │
        │                       │  (port 8001)                       │
        │ HTTP (OpenAI API)     └────────────────────────────────────┘
        ▼
┌──────────────────────────┐
│  vLLM retrieval backend  │
│  google/gemma-3-12b-it   │
│  (port 8002)             │
│  + Hearth & Pass system  │
│    prompt + knowledge    │
└──────────────────────────┘
```

## Directory layout

```
poc/
├── README.md                ← you are here
├── knowledge/
│   └── hearth_and_pass.json    Menu, hours, policies, FAQ, escalation rules
├── persona/
│   └── vox_system_prompt.md    Vox persona — voice, do/don'ts, ordering script
├── sidecar/                    FastAPI service
│   ├── pyproject.toml
│   ├── run.sh                  Start script (creates venv, installs, launches)
│   ├── app.py                  REST + SSE endpoints
│   ├── schema.py               Pydantic models (mirrored to web/lib/types.ts)
│   ├── intent.py               Transcript → order extraction
│   ├── pos_stub.py             Fake Toast write (logs JSON)
│   ├── state.py                In-memory call state + SSE broker
│   └── mock_transcript.py      Canned scenarios for offline demo
├── web/                        Next.js 15 + React 19 + Tailwind
│   ├── app/                    Page + layout
│   ├── components/             CallPane, BackstagePane, OrderTicket, ...
│   └── lib/                    Types, REST client, SSE hook
├── runpod/
│   ├── BRINGUP.md              GPU pod setup, env vars, port forwarding
│   ├── setup.sh                Install everything on a fresh pod
│   └── start.sh                Launch all 4 services in tmux
└── demo/
    ├── script.md               90-second investor walkthrough
    └── checklist.md            Pre-demo checks (mic, network, screen recording)
```

## Run modes

### Mock mode (laptop, no GPU)

For frontend development and a fallback "demo" if the GPU is unreachable. Drives the UI from canned conversation scripts.

```bash
# Terminal 1 — sidecar
cd poc/sidecar
bash run.sh

# Terminal 2 — web
cd poc/web
npm install   # first time only
npm run dev
# open http://localhost:3001
```

The default state of the page shows three "scenario" buttons (Order / Info / Escalate). Click any to play a canned conversation through the full pipeline — transcript bubbles, retrieval log, order ticket, latency panel, and POS write all populate as if a real call were happening.

### Live mode (RunPod with A100)

See [runpod/BRINGUP.md](runpod/BRINGUP.md). One-time setup, then:

```bash
cd /workspace/voxreach-poc/poc
bash runpod/start.sh
# open the RunPod public URL for port 3001
```

## What's mocked vs real in this POC

| Layer | POC | Production |
|---|---|---|
| Speech I/O | MoshiRAG full-duplex (real) | Same |
| RAG retrieval | vLLM + in-prompt knowledge (real) | Vector store + per-tenant knowledge |
| Intent extraction | Rule-based regex on transcript | Structured-output LLM call |
| POS write | `pos_stub.py` writes JSON to disk | Toast / Square / Clover adapter |
| Tenant isolation | Single tenant hardcoded | Per-tenant Neon branch + KMS key |
| Telephony | Browser mic | Twilio PSTN + Media Streams |
| Auth | None | Per-restaurant API keys |

The POC is honest about its scope. Every mocked layer has a clear production replacement documented in `VoxReach_Internal_Infra_Plan.md`.

## License

This POC code: MIT.

Model weights used at runtime are governed by their respective licenses:
- `kyutai/moshika-rag-pytorch-bf16` — CC-BY-4.0 (Kyutai)
- `google/gemma-3-12b-it` — Gemma terms (Google)

The "research only" disclaimer in MoshiRAG's model card is acknowledged. Acceptable for investor demos. **Not** to be deployed in front of paying pilot customers without further safety review per VoxReach's documented Trustworthy AI policy.
