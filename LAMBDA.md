# Lambda Labs runbook — VoxReach POC

**Use this for investor demos, customer pilots, or anywhere the Thunder audio jitter is a dealbreaker.** Lambda Labs gives you bare-metal GPU — no virtualization, no shared cores — so moshi streams audio smoothly at 80ms cadence instead of bursting.

For day-to-day development, use [THUNDER.md](THUNDER.md) — it's cheaper.

---

## When to use Lambda vs Thunder

| | Thunder | Lambda |
|---|---|---|
| Cost (A100 80GB) | ~$1.50/hr | ~$1.29/hr |
| Cost (H100 80GB) | n/a | ~$2.49/hr |
| GPU sharing | Yes (virtualized) | No (bare metal) |
| Audio jitter | Significant on 2-min calls | Minimal |
| Snapshot/resume | Yes (THUNDER.md) | No native snapshots — provision fresh |
| Best for | Dev, mock-mode testing, persona iteration | Live demos, customer calls, production-like testing |

---

## 1. Pick an instance

On the Lambda Labs Cloud dashboard, pick:

| Option | $/hr | When |
|---|---|---|
| **gpu_1x_h100_pcie** (H100 80GB, 26 vCPU, 200 GB RAM) | $2.49 | **Recommended.** ~2× moshi throughput. Smooth audio. |
| **gpu_1x_a100_sxm4** (A100 80GB) | $1.29 | If H100 is out of stock or budget-tight |
| **gpu_1x_a100** (A100 PCIe 40GB) | $1.10 | Marginal — only 40 GB VRAM, tight after persona + ollama |

Tip: filter by **Availability: On-demand** and prefer regions closest to you (us-west-1 / us-east-1 / europe-central-1) — every 50ms of network round-trip compounds the perceived latency.

---

## 2. Initial setup (one-time per instance)

Lambda instances come with a public IP — no tunneling CLI needed. SSH directly:

```bash
# On your laptop
ssh ubuntu@<lambda-public-ip>
```

Lambda's base image has Ubuntu 22.04 + CUDA preinstalled, similar to Thunder.

```bash
# 1. Confirm hardware
nproc                                          # expect 26 on H100 instance
free -h | head -3
nvidia-smi --query-gpu=name,memory.free --format=csv

# 2. Workspace path — Lambda uses /home/ubuntu by default, not /workspace
#    We'll create /workspace symlink so setup.sh / start.sh work unchanged
sudo mkdir -p /workspace
sudo chown ubuntu:ubuntu /workspace
echo "WORKSPACE=/workspace" >> ~/.bashrc

# 3. Set your token
export HF_TOKEN=hf_yourtoken

# 4. Clone repo and run setup
cd /workspace
git clone https://github.com/kfjiztom/voxreach-poc.git
cd voxreach-poc
tmux new -s setup
bash runpod/setup.sh 2>&1 | tee /workspace/setup.log
# Detach: Ctrl+b then d
```

`detect_hw.py` will auto-pick the H100 profile (TORCH_CUDA_ARCH_LIST=9.0, extract_timeout=20s) — no code changes needed.

Setup time on Lambda H100: **~8 minutes** (faster than Thunder due to better disk I/O).

---

## 3. Browser access — direct, no tunnel

Unlike Thunder's tnr CLI, Lambda gives every instance a public IP with all ports open by default. You can hit the services directly:

```
http://<lambda-public-ip>:3001    # Web demo
http://<lambda-public-ip>:8001    # Sidecar API
http://<lambda-public-ip>:8998    # PersonaPlex native UI
```

**Security note:** these ports are PUBLIC. For a demo this is fine, but for anything sensitive add SSH tunneling:

```bash
# On your laptop — same UX as Thunder but using SSH
ssh -L 3001:localhost:3001 -L 8001:localhost:8001 -L 8998:localhost:8998 ubuntu@<lambda-public-ip>
# Now browse to http://localhost:3001 on your laptop
```

---

## 4. Resume from existing state (rsync from Thunder snapshot)

Lambda doesn't have native snapshots like Thunder, but you can rsync from any other working instance:

```bash
# On your laptop or a "snapshot" Thunder instance you keep around:
rsync -avz --progress \
  /workspace/.cache /workspace/ollama-models /workspace/personaplex \
  ubuntu@<lambda-public-ip>:/workspace/

# Then on Lambda:
cd /workspace && git clone https://github.com/kfjiztom/voxreach-poc.git
export HF_TOKEN=hf_...
bash voxreach-poc/runpod/start.sh
```

The cache transfer is ~35 GB (HuggingFace cache 23 GB + Ollama 11 GB + Piper voices). Over Lambda's 1-10 Gbps network this takes ~10 minutes.

---

## 5. Launch services

```bash
export HF_TOKEN=hf_<your_token>
export NEXT_PUBLIC_PERSONAPLEX_URL='http://localhost:8998'
export NEXT_PUBLIC_MOCK_MODE='false'
export VOXREACH_CUSTOMER_STT_ENABLED=1

cd /workspace/voxreach-poc && git pull
bash runpod/start.sh
```

Wait 3-5 minutes for moshi to load on H100 (faster than A100). Then verify:

```bash
ss -tlnp 2>/dev/null | grep -E ':(3001|8001|8998)'
curl -s http://localhost:8001/api/state | head
tmux capture-pane -t voxreach:personaplex -p -S -50 | grep 'Running on'
```

---

## 6. Cost-conscious workflow

Lambda bills by the **second**. Stop the instance between demos:

```bash
# On laptop
ssh ubuntu@<ip> 'sudo shutdown now'
# Or use Lambda dashboard "Terminate" button
```

When you terminate (vs stop), you lose the disk. So:
- **During an active POC week**: spin up once, run for the week, terminate when done. $2.49 × 168 hrs = $418 for the full week.
- **For one-off demos**: spin up the morning of, run for 4 hours, terminate. $2.49 × 4 = $10/demo.

---

## 7. Verification — what "healthy on Lambda" looks like

```bash
# Trivial Ollama latency — must be sub-1s warm
time curl -s http://localhost:11434/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"qwen2.5:3b","messages":[{"role":"user","content":"reply with one word: ok"}],"stream":false}' \
  | head -c 100

# moshi should be 100% GPU
ollama ps
nvidia-smi --query-gpu=utilization.gpu --format=csv
```

After a 2-minute live call:
- **Audio played**: ~2:00 (should be ~ wall-clock time of call)
- **Missed audio**: under 0:02 (should be near-zero — that's the bare-metal win)
- **Latency**: under 1-2 seconds (vs Thunder's 16-24s)
- **Buffer min/max**: 0.000 / 0.020 (much tighter than Thunder)

If you see Thunder-grade numbers (10+ seconds latency, 10+ seconds missed) on Lambda, something is wrong — usually network buffering or the wrong instance type. Recheck `nvidia-smi` shows the right GPU.

---

## 8. Known Lambda Labs quirks

| Issue | Workaround |
|---|---|
| No native snapshots | rsync from Thunder or another Lambda instance |
| No persistent volumes (separate from instance disk) | Use S3 or external storage for big artifacts |
| Public IPs by default — ports exposed | SSH tunnel for non-demo use |
| Region availability shifts | Have 2-3 region preferences ready; H100s sometimes sell out us-west |
| `sudo` works without password | Great for setup, be careful in production |
| /workspace doesn't exist by default | We symlink it in step 2 above |

---

## 9. Going back to Thunder after a demo

Lambda's terminate destroys the disk. To resume on Thunder afterward:

```bash
# Start instance from your latest Thunder snapshot
# (same flow as THUNDER.md §4 "Resume from snapshot")
```

No bidirectional state syncing needed — your code lives in git, your knowledge base lives in the sidecar's hardcoded JSON, and Lambda is just an ephemeral compute layer.

---

## 10. Cost comparison: Thunder dev + Lambda demos

| Pattern | Thunder | Lambda | Total/month |
|---|---|---|---|
| 6 hrs/day dev (Thunder) + 1 demo/week (4 hrs Lambda H100) | ~$270 | ~$40 | **$310** |
| 24/7 Thunder + 1 demo/week Lambda | ~$1080 | ~$40 | $1120 |
| 24/7 Lambda H100 (no Thunder) | n/a | ~$1800 | $1800 |

The split-environment approach is ~6× cheaper than running production-grade always-on.
