# RunPod bring-up — VoxReach POC

The whole demo runs on a single RunPod GPU pod. The frontend can also be developed offline (Mac), but the live audio demo needs the pod.

## 1. Pod template

| Setting | Value | Notes |
|---|---|---|
| GPU | **A100 80GB PCIe** (or **H100 80GB**) | 80 GB is the floor — Moshi-RAG ~16 GB + Gemma-3-12B retrieval LLM ~24 GB + headroom |
| Template | RunPod `PyTorch 2.4.0 · py3.12 · cuda 12.4` | Or any image with CUDA 12.x + Python ≥ 3.10 |
| Disk | 80 GB container, 80 GB volume | Moshi weights ~14 GB + Gemma-3-12B ~24 GB + pip caches |
| Exposed ports | `8998` (Moshi), `8001` (sidecar), `3001` (web) | RunPod will assign public proxied URLs |
| Env | `HF_TOKEN=hf_...` | Set in the pod's secrets, do not bake into the image |

Region: pick one with A100 80GB spot availability — `us-central` or `eu-romania` usually best. Spot is fine for the POC; an interruption mid-demo just means re-running the bring-up script.

## 2. One-time setup (per pod)

SSH into the pod, then:

```bash
# 1. Clone the POC into the pod
git clone <YOUR_REPO_URL_HOSTING_THIS_POC> /workspace/voxreach-poc
cd /workspace/voxreach-poc/poc

# 2. Run the setup script (installs system deps + clones moshi-rag + pulls model weights)
bash runpod/setup.sh
```

The setup script will:
- `apt install libopus-dev ffmpeg`
- Create `/workspace/voxreach-poc/poc/.venv` and install Python deps
- Clone `kyutai-labs/moshi-rag` into `/workspace/moshi-rag`
- Run `huggingface-cli login` if `$HF_TOKEN` is not already set in the env
- Pre-download `kyutai/moshika-rag-pytorch-bf16` and `google/gemma-3-12b-it` weights
- Install Node 20 + run `npm ci` for the web app

Expect 15–25 minutes for first-run weight downloads.

## 3. Start the stack

```bash
cd /workspace/voxreach-poc/poc
bash runpod/start.sh
```

This spawns four services in named `tmux` windows (so you can `tmux attach` and see logs):

| Window | Service | Port | Purpose |
|---|---|---|---|
| `vllm` | vLLM serving `google/gemma-3-12b-it` | 8002 | Retrieval back-end LLM |
| `moshi` | `python -m moshi.moshi.server --hf-repo kyutai/moshika-rag-pytorch-bf16` | 8998 | Full-duplex speech model + built-in audio path |
| `sidecar` | uvicorn FastAPI on `sidecar/app.py` | 8001 | Transcript watcher · POS stub · SSE |
| `web` | `next start` (or `next dev` for hot reload) | 3001 | The branded 2-pane demo UI |

`start.sh` waits for vLLM to be healthy before launching moshi-rag (moshi-rag will retry retrieval calls but cleaner to start in order).

## 4. Verify

From your laptop, open RunPod's public-proxy URL for **port 3001** — that's the Hearth & Pass demo page. You should see:

1. Left pane: "Call Hearth & Pass", idle state, "Start call" button
2. Right pane: empty backstage panels with placeholder copy

Click **Start call**, grant mic access, say *"Can I place an order?"* — within ~1–2 seconds Vox should reply.

## 5. Demo modes

Two environment overrides on the **web** service control what the frontend talks to:

```bash
# Mock mode (default — works without GPU, for laptop dev or fallback demo):
NEXT_PUBLIC_MOCK_MODE=true

# Live mode (real Moshi audio on the pod):
NEXT_PUBLIC_MOCK_MODE=false
```

In live mode, `Start call` opens a WebSocket to the Moshi server on port 8998 and uses its native audio plumbing (forked from `kyutai-labs/moshi-rag` client). The sidecar still drives the right-pane backstage panels — it subscribes to Moshi's transcript stream.

## 6. Cost expectations

| Item | $/hr | Notes |
|---|---|---|
| A100 80GB PCIe spot (RunPod) | ~$1.19 | Spot — interruptible, cheap |
| A100 80GB PCIe on-demand | ~$1.89 | Use on-demand for live investor calls |
| H100 80GB PCIe on-demand | ~$2.49 | Alternative; slightly faster |
| Storage (80 GB persistent) | ~$0.08/hr | Keeps weights warm between sessions |

**Stop the pod when you're not demoing.** Persistent volume keeps weights — next bring-up is a 2-minute boot, not a 25-minute download.

## 7. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Moshi server OOMs at startup | vLLM ate too much VRAM | Lower vLLM `--gpu-memory-utilization` to 0.55 |
| First-audio latency > 2 s consistently | Cold weights | Confirm `--preload` flag in `start.sh`; re-run |
| Sidecar SSE drops every ~60s | RunPod's HTTP proxy idle timeout | Use raw TCP port-forward via `ssh -L 8001:localhost:8001` instead of the proxy |
| Browser can't connect to mic | RunPod proxy is HTTP not HTTPS | Use `https://` URL from RunPod console — Chrome blocks mic on `http://` |
| `huggingface-cli: 403` on weights | Forgot to accept the license | Visit `https://huggingface.co/kyutai/moshika-rag-pytorch-bf16` in browser → "Agree" |
