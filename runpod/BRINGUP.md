# RunPod bring-up — VoxReach POC (PersonaPlex variant)

The whole demo runs on a single RunPod GPU pod. The frontend can also be developed offline (Mac), but the live audio demo needs the pod.

> **Active stack:** NVIDIA PersonaPlex (`nvidia/personaplex-7b-v1`), single AI service, NVIDIA Open Model License.
> **Parked stack:** MoshiRAG + vLLM (Phase B target — see `git log` for previous variants).

## 1. Pod template

| Setting | Value | Notes |
|---|---|---|
| GPU | **24 GB minimum** — RTX 4090, A10G, A40, L40S all work. A100 80GB if you have headroom budget. | NVIDIA's PersonaPlex docs spec 24 GB VRAM minimum (model is ~14 GB; rest is KV cache + headroom). H100 80GB also fine. |
| Template | RunPod `PyTorch 2.4.0 · py3.12 · cuda 12.4` (or newer) | CUDA 12.x required |
| Container disk | **30 GB** (default on most templates) | Holds OS + base PyTorch + venvs + weights — onboard storage, no network volume needed |
| Network volume | **Not required** for this variant | Skip the MooseFS slowness |
| Exposed ports | `8998` (PersonaPlex), `8001` (sidecar), `3001` (web) | RunPod assigns public proxied URLs |
| Env | `HF_TOKEN=hf_...` | Set in the pod's secrets, do not bake into the image |
| SSH | Enabled | Required for the bring-up |

Region: any with A100 80GB stock — `us-ks-2`, `us-ca-2`, or `eu-ro-1` usually green.

## 2. License acceptance (one-time, on your laptop)

1. Visit [huggingface.co/nvidia/personaplex-7b-v1](https://huggingface.co/nvidia/personaplex-7b-v1)
2. Sign in with the same HF account that owns your `HF_TOKEN`
3. Click **Agree and access repository**

This is account-level — you only do it once across all your RunPod work.

## 3. One-time setup (per persistent volume)

SSH into the pod, then:

```bash
# 1. Confirm the network volume is mounted
df -h /workspace                       # should show ~50 GB

# 2. Make sure HF_TOKEN is exported
export HF_TOKEN="<your-token>"
echo "HF_TOKEN length: ${#HF_TOKEN}"   # should print 37

# 3. Clone the POC into the persistent volume (public repo — no auth)
cd /workspace
git clone https://github.com/kfjiztom/voxreach-poc.git
cd voxreach-poc

# 4. Run setup inside tmux so SSH disconnect doesn't kill it
tmux new -s setup
bash runpod/setup.sh 2>&1 | tee /workspace/setup.log
# Detach: Ctrl+b then d. Reattach later: tmux attach -t setup.
```

The setup script will:
- Set up persistent cache locations (`/workspace/.cache/huggingface`, `/workspace/.cache/pip`)
- Write `/workspace/.voxreach.env` with all path variables for future pods to source
- `apt install libopus-dev ffmpeg tmux jq curl git`
- Upgrade pip
- Create sidecar venv at `/workspace/voxreach-poc/sidecar/.venv` and install FastAPI + uvicorn deps
- Clone `NVIDIA/personaplex` to `/workspace/personaplex`
- Create PersonaPlex venv at `/workspace/.venv/voxreach-personaplex` and install NVIDIA's moshi fork
- Verify CUDA works (fails fast if not)
- Pre-download `nvidia/personaplex-7b-v1` weights (~14 GB to `/workspace/.cache/huggingface`)
- Install Node 20 + run `npm ci` for the web app

Total: ~10-15 minutes on a fresh pod.

## 4. Start the stack

```bash
cd /opt/voxreach-poc
bash runpod/start.sh
tmux attach -t voxreach
```

This spawns three tmux windows:

| Window | Service | Port | Purpose |
|---|---|---|---|
| `personaplex` | `python -m moshi.server --hf-repo nvidia/personaplex-7b-v1` | 8998 | Full-duplex S2S — the AI |
| `sidecar` | uvicorn FastAPI on `sidecar/app.py` | 8001 | Transcript watcher + POS stub + SSE |
| `web` | `next start` | 3001 | The branded 2-pane demo UI |

Use `Ctrl+b 0/1/2` to switch between windows.

## 5. Verify

Once all three windows show their startup banners, in another SSH session:

```bash
echo "=== ports ==="
ss -tlnp 2>/dev/null | grep -E ":3001|:8001|:8998"

echo "=== personaplex health ==="
curl -sk https://localhost:8998 | head -5

echo "=== sidecar health ==="
curl -s http://localhost:8001/api/state

echo "=== web health ==="
curl -sw "%{http_code}\n" -o /dev/null http://localhost:3001/
```

Web should return 200, sidecar should return JSON or `null`, personaplex should return its index page.

## 6. Open the demo in a browser

In the RunPod console, click your pod's **Connect** tab and find port **3001** (HTTP). Open the proxied URL — that's the Hearth & Pass demo page. Grant mic permission and start a call.

## 7. Cost expectations

| Item | $/hr |
|---|---|
| A100 80GB PCIe spot (RunPod) | ~$1.19 |
| A100 80GB PCIe on-demand | ~$1.89 |
| Container disk only (no network volume) | included |

**Stop the pod when you're not demoing.** Without a network volume, restart means re-downloading the 14 GB weights — call it 5-8 min on a fresh pod. Trade-off accepted for simpler setup.

## 8. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `setup.sh` exits at HF check | `HF_TOKEN` not exported | `export HF_TOKEN=hf_...` and re-run |
| `setup.sh` exits at CUDA check | torch can't talk to GPU | check `nvidia-smi`; pod GPU may not be initialized |
| PersonaPlex 401 on weight pull | License not accepted on HF page | accept at huggingface.co/nvidia/personaplex-7b-v1, retry |
| Sidecar `ModuleNotFoundError` | sidecar venv didn't get created | `cd sidecar && bash run.sh` (it creates the venv) |
| Web `next: not found` | npm ci didn't run | `cd web && npm ci` |
| Browser can't connect to mic | RunPod proxy is HTTP not HTTPS | Use `https://` URL from RunPod console — Chrome blocks mic on `http://` |
| `df -h /` shows >85% on container disk | weights downloaded successfully but OS overhead is high | check `du -sh /var/lib/docker /var/cache/apt` and clean if huge |

## 9. Why we parked MoshiRAG

Original plan was MoshiRAG (Moshi + asynchronous RAG) + vLLM serving Gemma-3-12B as the retrieval back-end. Spent significant time hitting cascading dependency conflicts on RunPod: torch CUDA wheel mismatches (cu124 vs cu128 vs cu130), vLLM/moshi/transformers/xformers version incompatibilities, MooseFS network-FS install slowness.

Pivoted to PersonaPlex for the POC because:
- One service instead of three
- One model weights repo (no separate retrieval LLM)
- No torch/vllm/transformers dep coordination
- Voice quality identical (same Moshi underneath)
- Faster iteration to a working demo

MoshiRAG remains the Phase B production architecture per `VoxReach_Internal_Infra_Plan.md`. Revisit when MoshiRAG packaging matures.
