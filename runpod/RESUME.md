# RunPod resume — VoxReach POC (PersonaPlex variant)

Use this when you've **stopped** the pod, **terminated and re-created** with the same network volume attached, or when RunPod recycled your pod onto new hardware.

Everything that survives pod loss lives on the **persistent network volume at `/workspace`**: venvs, model weights, the NVIDIA personaplex source, pip caches, and the env file. The container disk and any installed apt packages will be re-installed automatically on the new pod.

---

## On the new pod — bring everything back up

Three commands once the pod is "Running" and the network volume is attached:

```bash
# 1. SSH in (pod ID will be different — get from RunPod console Connect tab)
ssh <new-pod-id>@ssh.runpod.io -i ~/.ssh/runpod

# 2. Source the persistent env file (sets PP_VENV, HF_HOME, etc.)
source /workspace/.voxreach.env

# 3. Re-export your HF token (this is per-pod, not on the volume)
export HF_TOKEN="<your-token>"

# 4. Re-install the apt-level system packages (these are ephemeral)
apt-get update -qq && apt-get install -y -qq libopus-dev ffmpeg tmux jq curl git nodejs
```

Then start the stack:

```bash
cd /workspace/voxreach-poc

# Pull latest commits in case anything got fixed on main
git pull

# Optional: if you want the iframe-mode demo, set this with your NEW pod's URL
export NEXT_PUBLIC_PERSONAPLEX_URL="https://<new-pod-id>-8998.proxy.runpod.net"

# Launch all three services in tmux
bash runpod/start.sh
tmux attach -t voxreach
```

After ~90 seconds (PersonaPlex loads weights into VRAM, web rebuilds with the new URL), all three windows should be healthy.

---

## What's on the volume vs ephemeral

| Item | Lives where | Survives pod recycle? |
|---|---|---|
| Repo clone (`/workspace/voxreach-poc`) | `/workspace` | ✅ |
| NVIDIA personaplex source (`/workspace/personaplex`) | `/workspace` | ✅ |
| PersonaPlex venv (`/workspace/.venv/voxreach-personaplex`) | `/workspace` | ✅ |
| Sidecar venv (`/workspace/voxreach-poc/sidecar/.venv`) | `/workspace` | ✅ |
| HF model cache (`/workspace/.cache/huggingface`) | `/workspace` | ✅ |
| pip cache (`/workspace/.cache/pip`) | `/workspace` | ✅ |
| Env file (`/workspace/.voxreach.env`) | `/workspace` | ✅ |
| apt-installed system packages | `/usr` | ❌ (re-installed on new pod) |
| Node binary | `/usr` | ❌ |
| HF_TOKEN | env only | ❌ (re-export) |
| Running processes (PersonaPlex, sidecar, web) | RAM | ❌ |

---

## If the pod is brand new (first time on a fresh volume)

If `/workspace/.voxreach.env` doesn't exist, you need the full setup:

```bash
# Make sure HF_TOKEN is set
export HF_TOKEN="<your-token>"

# Clone the repo to the volume (public repo — no auth needed)
cd /workspace
git clone https://github.com/kfjiztom/voxreach-poc.git
cd voxreach-poc

# Run setup (~10-15 min — installs everything on /workspace)
tmux new -s setup
bash runpod/setup.sh 2>&1 | tee /workspace/setup.log
# Detach: Ctrl+b then d
```

Once `==> Setup complete` prints, future pods follow the resume flow above.

---

## Common gotcha — nginx squatting on ports

RunPod runs nginx as a "placeholder" on exposed ports until a real service binds. `start.sh` now kills it automatically if detected. If you ever hit `EADDRINUSE` on `:3001` or `:8001`:

```bash
pkill -x nginx
sleep 1
ss -tlnp 2>/dev/null | grep -E ":3001|:8001"   # should be empty
bash runpod/start.sh
```

---

## If the PersonaPlex iframe URL needs to change

The pod's RunPod proxy URL changes every time you destroy/recreate the pod. The Next.js build bakes the URL in at build time, so you need to rebuild whenever it changes:

```bash
cd /workspace/voxreach-poc/web
export NEXT_PUBLIC_PERSONAPLEX_URL="https://<new-pod-id>-8998.proxy.runpod.net"
export NEXT_PUBLIC_MOCK_MODE=true
export SIDECAR_URL=http://localhost:8001
npm run build
# Then restart the web window in tmux, or kill the tmux session and re-run start.sh
```

---

## Verify everything is up

```bash
echo "=== ports ==="
ss -tlnp 2>/dev/null | grep -E ":3001|:8001|:8998"

echo "=== sidecar health ==="
curl -s http://localhost:8001/api/state

echo "=== web health ==="
curl -sw "%{http_code}\n" -o /dev/null http://localhost:3001/

echo "=== personaplex health ==="
curl -sk "https://localhost:8998" | head -1
```

Healthy looks like:
- Three services on the right ports
- Sidecar returns `null` or JSON (NOT HTML)
- Web returns `200`
- PersonaPlex returns HTML

---

## Re-injecting the persona prompt

If you injected the Vox prompt via PersonaPlex's web UI and it doesn't persist:

1. Open `https://<pod-id>-8998.proxy.runpod.net`
2. Find the system-prompt / instructions text field
3. Paste contents of `persona/vox_personaplex_prompt.txt`
4. Start the call

If you find the right CLI flag for `moshi.server` to load the prompt at server startup, push that fix to `start.sh` so it survives the next pod recycle automatically.

---

## When to terminate vs stop

| Action | When |
|---|---|
| **Stop** | Done for the day. Container disk preserved on pod-specific volume, ~$5/month while stopped, restart in 30 sec. |
| **Terminate** | Done with this pod, or pod is broken. Container disk wiped. With network volume attached, your work survives — bring up a new pod with the same volume. |
| **Hardware recycle** (RunPod's choice) | Out of your control. With network volume, you just bring up a new pod. |

The persistent network volume is what makes pod-loss recoverable in minutes instead of an hour.
