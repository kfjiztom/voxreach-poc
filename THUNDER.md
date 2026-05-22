# Thunder Compute runbook — VoxReach POC

**Read this every time** you spin up a new Thunder instance (cold start) or resume from a snapshot.

The previous infra was RunPod. The `runpod/` directory keeps that name for git-history continuity, but every script in there now targets Thunder Compute. The legacy [runpod/BRINGUP.md](runpod/BRINGUP.md) and [runpod/RESUME.md](runpod/RESUME.md) docs are kept for historical reference only — this file is the source of truth.

---

## TL;DR — resume from snapshot in 3 steps

1. **Thunder dashboard:** create new instance from snapshot `voxreach-may18-qwen25-piper-ready` (or the latest you have). Wait for "Running".
2. **Laptop:** open the tunnel — `tnr connect <new-instance-id> -t 3001 -t 8001 -t 8998`.
3. **SSH in:** run the [Resume from snapshot](#resume-from-snapshot) checklist below.

---

## 1. Instance spec (the spec that works)

| Setting | Value | Notes |
|---|---|---|
| Provider | Thunder Compute | tnr CLI for tunneling |
| GPU | **A100-SXM4-80GB** | A100 80 GB is the validated baseline |
| vCPUs | **8 minimum** | 4 vCPU caused 39s Ollama latency due to CPU contention between moshi + ollama + system |
| RAM | **64 GB** | Peak active usage ~8–10 GB; the rest is OS page cache |
| Disk | ~100 GB | Snapshot working size is ~40 GB (models + venvs + repo) |
| OS | Ubuntu 22.04 | Python 3.12, NVIDIA driver 580.x |
| Persistent path | `/workspace` | Holds venvs, models, repo, env file |

**Why 8 vCPU and not 12:** peak CPU need is ~5–7 cores (moshi 2–3 + ollama 1–2 + sidecar 0.5 + web 0.3 + Piper 0.5 + system 0.5). 8 covers it with 1–2 spare. 12 would sit idle.

---

## 2. Snapshot strategy

| Action | When |
|---|---|
| **Create snapshot** | After every working session that landed real code/model changes. Names should encode date + key state, e.g. `voxreach-may18-qwen25-piper-ready`. |
| **Stop instance** | End of day. Stops GPU billing. Snapshot is independent of instance state. |
| **Terminate instance** | After a successful snapshot is confirmed "Ready". Frees disk and stops all billing. |
| **Create from snapshot** | Start of every session. Always pick the newest verified snapshot. |

**Never** terminate before confirming the snapshot is in "Ready" state.

---

## 3. First-time setup (cold-start, no snapshot)

Only needed if you're on a fresh blank instance with no `/workspace/.voxreach.env`.

```bash
# 1. Set token (Thunder doesn't persist env across reboots by default)
export HF_TOKEN="<your-token>"

# 2. Clone repo onto persistent disk
cd /workspace
git clone https://github.com/kfjiztom/voxreach-poc.git
cd voxreach-poc

# 3. Run full setup inside tmux (so an SSH disconnect won't kill it)
tmux new -s setup
bash runpod/setup.sh 2>&1 | tee /workspace/setup.log
# Detach: Ctrl+b then d. Reattach: tmux attach -t setup
```

`setup.sh` will:
- Install apt packages (`libopus-dev ffmpeg tmux jq curl git lshw pciutils`)
- Create the unversioned `libcuda.so` symlink (Triton's gcc needs it for torch.compile)
- Build persistent caches at `/workspace/.cache/{huggingface,pip}`
- Create venvs at `/workspace/.venv/voxreach-personaplex` and `/workspace/voxreach-poc/sidecar/.venv`
- Clone NVIDIA `personaplex` to `/workspace/personaplex` and apply our patches
- Pre-download PersonaPlex 7B weights (~14 GB) + Piper voice files
- Install Node 20 + run `npm ci` in `web/`
- Run `detect_hw.py` to write `/workspace/.voxreach-hw.env`

Total: ~10–15 minutes on a healthy A100 instance.

Take a snapshot immediately after this finishes successfully.

---

## 4. Resume from snapshot

Most sessions start here.

```bash
# (0) Fix ownership — `sudo` operations during setup leave root-owned files
sudo chown -R ubuntu:ubuntu /workspace /home/ubuntu 2>/dev/null

# (1) Confirm hardware survived the snapshot restore
nproc                                                       # expect 8
free -h | head -3                                           # expect ~64Gi
nvidia-smi --query-gpu=name,memory.free --format=csv         # expect A100 + ~80 GB free
du -sh /workspace/.cache /workspace/ollama-models           # confirm models present

# (2) Re-export env (Thunder doesn't persist HF_TOKEN or NEXT_PUBLIC_* across reboots)
export HF_TOKEN="<your-token>"
export NEXT_PUBLIC_PERSONAPLEX_URL='http://localhost:8998'
export NEXT_PUBLIC_MOCK_MODE='false'
export VOXREACH_CUSTOMER_STT_ENABLED=1     # ← critical, default is 0

# (3) Pull latest commits in case anything got fixed on main
cd /workspace/voxreach-poc && git pull

# (4) Launch all three services in tmux
bash runpod/start.sh
# Attach with: tmux attach -t voxreach
```

After `start.sh` exits, you should see:

```
==> Services launching in tmux session 'voxreach'.
==> Local URLs (this pod):
    Web demo:        http://localhost:3001
    PersonaPlex UI:  http://localhost:8998
    Sidecar:         http://localhost:8001/api/state
```

Give moshi ~2–4 minutes to load weights into VRAM. It's ready when the personaplex window shows `Running on http://0.0.0.0:8998`.

---

## 5. Tunnel from your laptop

The Thunder instance is reachable only via `tnr connect`. On your **laptop**, not the instance:

```bash
# List your Thunder instances
tnr list

# Forward all three ports we need (3001 web, 8001 sidecar, 8998 personaplex)
tnr connect <new-instance-id> -t 3001 -t 8001 -t 8998
```

Once connected, `http://localhost:3001` on your laptop reaches the web demo. The iframe at `:8998` and the sidecar at `:8001` both load through the same tunnel.

**If the iframe doesn't load:** check that `-t 8998` is in your `tnr connect` flags. Without it, the session closes immediately after the WebSocket handshake.

---

## 6. Verification — what "healthy" looks like

Run from inside the instance:

```bash
# All three ports occupied
ss -tlnp 2>/dev/null | grep -E ':(3001|8001|8998)' || sudo ss -tlnp | grep -E ':(3001|8001|8998)'

# Sidecar returns JSON null (no call yet) or call state
curl -s http://localhost:8001/api/state

# Personaplex returns its index HTML
curl -s http://localhost:8998 | head -3

# Web returns 200
curl -sw "%{http_code}\n" -o /dev/null http://localhost:3001/

# Ollama is on GPU, qwen2.5:3b loaded
ollama ps

# Trivial Ollama latency — must be under 1s warm
time curl -s http://localhost:11434/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"qwen2.5:3b","messages":[{"role":"user","content":"reply with one word: ok"}],"stream":false}' \
  | head -c 100
```

If the trivial Ollama call comes back in **<1 second warm**, the CPU contention issue is gone. If it's still 30s+, recheck `nproc` (must be 8) and `ollama ps` (must show 100% GPU).

---

## 7. Stopping for the night

```bash
# Kill all services cleanly
tmux kill-session -t voxreach 2>/dev/null
pkill -x ollama 2>/dev/null
sleep 2

# Confirm clean
ss -tlnp 2>/dev/null | grep -E ':(3001|8001|8998|11434)' || echo "all clean"
cd /workspace/voxreach-poc && git status     # should be clean
```

Then in the **Thunder dashboard**:

1. Create snapshot — name with date + key state
2. Wait for "Ready"
3. Stop the instance (or Terminate, if you trust the snapshot)

---

## 7a. Known limitation: audio jitter on long calls

**This is intrinsic to Thunder Compute's GPU virtualization and cannot be fixed at the moshi layer.** Expect it. Plan around it.

### Symptom

On calls longer than ~30 seconds, you'll see in the browser audio processor logs:

```
'Dropping packets' '200.0' '200.0'
'Packet dropped' '120.0'
Increased maxBuffer to 80.0
'Missed some audio' 96
'Increased partial buffer to 25.0'
```

And the Server Audio Stats panel will show:
- **Latency**: 5-25 seconds (grows with call duration)
- **Missed audio**: 2-15 seconds (10-15% of total)
- **Buffer**: pinned at maxBuffer cap of 80

### Why it happens

Thunder Compute virtualizes GPU access. When neighbor tenants grab cycles, moshi falls behind real-time. When they release, moshi generates audio *faster than real-time* to catch up. The browser's audio queue buffers the bursts, grows past its `maxBuffer` cap (default 80 chunks), and starts dropping packets.

The net effect: by the end of a 2-minute call, what you hear is 10-20 seconds behind what Vox is actually generating. The model has already moved past the conversation point the customer hears.

### Why it can't be fixed on Thunder

The audio loop in moshi.server is GPU-paced, not wall-clock-paced. Adding a `sleep(80ms)` after each generation step would break the model — moshi expects to consume audio frames as fast as they arrive. The jitter ultimately comes from the GPU scheduling layer, which Thunder controls, not us.

### What to do about it

| For | Use |
|---|---|
| Persona iteration, sidecar work, UI development | Thunder (you'll tolerate the jitter — it's a dev environment) |
| Investor demos | Lambda Labs H100 — see [LAMBDA.md](LAMBDA.md) |
| Customer pilots | Lambda Labs H100 or CoreWeave bare-metal |
| Production | Bare-metal H100 dedicated, OR architectural shift to MoshiRAG (Phase B) |

The model and our patches are correct. The infrastructure is the variable.

### How to verify it's the platform and not us

Quickest check: try the same `runpod/start.sh` flow on Lambda Labs (see [LAMBDA.md](LAMBDA.md)) and compare the audio stats. Identical code, identical persona, dramatically different audio behavior = platform-induced jitter.

---

## 8. Common issues

| Symptom | Cause | Fix |
|---|---|---|
| `nproc` shows 4, latency awful | Wrong instance SKU — picked the cheap one | Stop, recreate with 8 vCPU / 64 GB |
| `Permission denied` writing `.bashrc` / repo | `sudo` left root-owned files | `sudo chown -R ubuntu:ubuntu /workspace /home/ubuntu` |
| `git pull` says "dubious ownership" | Same root-ownership issue | Same chown as above |
| `HF_TOKEN not set` after `sudo bash start.sh` | sudo strips env by default | `sudo -E bash start.sh` OR drop the `sudo` |
| Web at :3001 reachable, but iframe blank | tnr tunnel missing `-t 8998` | Add it to the `tnr connect` command |
| Vox connects then immediately disconnects | Same as above — iframe can't reach personaplex backend | Same fix |
| Order ticket stays empty during calls | `VOXREACH_CUSTOMER_STT_ENABLED=0` (the default) | Export `=1` before `start.sh` |
| Ollama call takes 10s+ even warm | Reasoning-mode model loaded (qwen3.x family) | `detect_hw.py` should select `qwen2.5:3b`; if not, override `VOXREACH_ORDER_MODEL=qwen2.5:3b` |
| `personaplex` tmux window died | Usually a crash on startup — check tmux logs | `tmux capture-pane -t voxreach:personaplex -p -S -200 \| tail -60` |
| nvidia-smi hangs / "All GPUs busy" | Thunder allocator lag on first boot from snapshot | Wait 30–60 seconds, retry |

---

## 9. Cost notes

| Item | $/hr (approx) |
|---|---|
| A100-SXM4-80GB / 8 vCPU / 64 GB | depends on Thunder pricing tier — check dashboard |
| Stopped instance | minimal (disk only) |
| Snapshot storage | minimal |

Stop the instance any time you're not actively using it. Snapshots keep state cheap.

---

## 10. Critical environment variables

| Var | Default | Set to | Reason |
|---|---|---|---|
| `HF_TOKEN` | — | `hf_<token>` | Pull PersonaPlex weights + STT models |
| `VOXREACH_CUSTOMER_STT_ENABLED` | `0` (off) | `1` | **Required** — without this no transcripts reach the sidecar and order extraction never runs |
| `NEXT_PUBLIC_PERSONAPLEX_URL` | unset | `http://localhost:8998` | Loads the personaplex iframe through the tnr tunnel |
| `NEXT_PUBLIC_MOCK_MODE` | `true` | `false` | Disables mock scenarios so the live call is what gets shown |
| `VOXREACH_ORDER_MODEL` | written by `detect_hw.py` | `qwen2.5:3b` | Auto-set per GPU profile; override only if detect_hw.py picks wrong |
| `VOXREACH_EXTRACTOR` | `auto` | `auto` | Auto-detects LLM vs rule-based |

---

## 11. Architecture cheatsheet

```
Your laptop browser
       │  (tnr tunnel: ports 3001, 8001, 8998)
       ▼
┌──────────────────────────────────────────────────────────────────────┐
│  Thunder Compute instance (A100 80GB, 8 vCPU, 64 GB)                 │
│                                                                      │
│   port 3001 — Next.js web (Hearth & Pass demo UI)                    │
│   port 8001 — FastAPI sidecar (transcripts, RAG, order, Piper TTS)   │
│   port 8998 — PersonaPlex / moshi.server (S2S voice)                 │
│   port 11434 — Ollama (qwen2.5:3b for order extraction)              │
│                                                                      │
│   GPU: PersonaPlex ~20 GB + qwen2.5:3b ~4 GB + STT whisper ~2 GB     │
│   CPU: moshi 2-3 cores, ollama 1-2 cores, others 1-2 cores           │
└──────────────────────────────────────────────────────────────────────┘
```

Piper TTS for order readback runs CPU-only — keeps moshi's GPU free for the real-time conversation.
