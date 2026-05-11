# RunPod resume — VoxReach POC (PersonaPlex variant)

Use this when you've **stopped** the pod (not terminated) and want to bring everything back up.

If you terminated the pod, follow `BRINGUP.md` from scratch instead.

---

## Tonight before you stop the pod

```bash
# 1. Confirm everything is on GitHub
cd /opt/voxreach-poc
git status
# Should print: "nothing to commit, working tree clean"
# If anything modified, commit/push it first.

# 2. (Optional) Take a screen recording of the working demo as insurance
# QuickTime / OBS / browser screen capture — 60-90 seconds is fine

# 3. Note your current PersonaPlex URL
# (Find in RunPod console → Connect → port 8998. Should be:
#  https://<POD-ID>-8998.proxy.runpod.net)
echo "Pod ID:" $(hostname)   # not the proxy URL but useful for reference
```

Then in the RunPod web console: click **Stop** on the pod. Cost drops to ~$5/month for the 50 GB container disk while stopped. **Never click Terminate** unless you actually want to nuke everything.

---

## Tomorrow — start the pod and bring services up

Three commands once the pod is back to "Running":

```bash
# 1. SSH in
ssh <pod-id>@ssh.runpod.io -i ~/.ssh/runpod
# (pod-id may be different after restart — check RunPod console Connect tab)

# 2. cd to the repo and pull any new commits
cd /opt/voxreach-poc
git pull

# 3. Bring everything up
bash runpod/start.sh
tmux attach -t voxreach
```

After ~60 seconds (PersonaPlex needs to load weights into VRAM), all three windows should be healthy:
- `personaplex` window: `Listening on 0.0.0.0:8998`
- `sidecar` window: `Uvicorn running on http://0.0.0.0:8001`
- `web` window: `Ready in Xms`

---

## If `start.sh` fails because nginx is squatting on the ports

This is the RunPod placeholder issue we hit. Quick fix:

```bash
pkill -x nginx
sleep 1
ss -tlnp 2>/dev/null | grep -E ":3001|:8001"   # should be empty
bash runpod/start.sh   # retry
```

---

## Re-injecting the persona prompt (if PersonaPlex resets it)

If you injected the Vox prompt via the PersonaPlex web UI yesterday and it didn't persist:

1. Open your PersonaPlex URL: `https://<POD-ID>-8998.proxy.runpod.net`
2. Find the system-prompt / instructions / persona text field
3. Paste the contents of `persona/vox_personaplex_prompt.txt` into it
4. Start the call

---

## Bringing up the branded iframe shell

The web app needs `NEXT_PUBLIC_PERSONAPLEX_URL` baked in at build time. Whenever the pod's URL changes:

```bash
cd /opt/voxreach-poc/web
export NEXT_PUBLIC_PERSONAPLEX_URL="https://<NEW-POD-ID>-8998.proxy.runpod.net"
export NEXT_PUBLIC_MOCK_MODE=true
export SIDECAR_URL=http://localhost:8001

npm run build
nohup npm run start > /tmp/web.log 2>&1 &
sleep 6
curl -sw "%{http_code}\n" -o /dev/null http://localhost:3001/
```

If the pod ID is the same as yesterday, you can skip the rebuild — `start.sh` will reuse the existing `.next/` build.

---

## Verify everything is up before opening the demo URL

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

You want:
- Three services on the right ports
- Sidecar returns `null` or JSON (not HTML)
- Web returns 200
- PersonaPlex returns HTML (its UI)

---

## Demo URLs to open

In RunPod console → Connect tab, two URLs matter:

| Port | Purpose | Where to find |
|---|---|---|
| **3001** | The branded Hearth & Pass shell — **this is the URL you share** | RunPod Connect tab |
| 8998 | Direct PersonaPlex UI (for paste-the-prompt access, fallback) | RunPod Connect tab |

Open 3001 in a browser. The iframe inside will load 8998 automatically.

---

## What's saved vs lost across stop/start

| Item | Survives stop/start? | Notes |
|---|---|---|
| Cloned repo (`/opt/voxreach-poc`) | ✅ | On container disk |
| PersonaPlex venv (`/opt/voxreach-personaplex`) | ✅ | On container disk |
| NVIDIA personaplex source (`/opt/personaplex`) | ✅ | On container disk |
| HF cache + weights (`/root/.cache/huggingface`) | ✅ | On container disk |
| Web build output (`web/.next/`) | ✅ | On container disk |
| `npm run start` background process | ❌ | Need to restart via `start.sh` |
| Sidecar process | ❌ | Need to restart via `start.sh` |
| PersonaPlex server process | ❌ | Need to restart via `start.sh` |
| tmux sessions | ❌ | `start.sh` recreates them |
| Persona prompt injected via web UI | **Possibly ❌** | Re-paste from `persona/vox_personaplex_prompt.txt` if Vox seems generic |
| Browser mic permission | ✅ | If same domain (proxy URL) |

Total time from "Start" to "demo URL responds 200": **about 90 seconds** (pod warm-up ~30s, PersonaPlex weight load ~30-60s, sidecar + web ~5s).

---

## When to terminate vs stop

| Action | When |
|---|---|
| **Stop** | Done for the day / between demos. ~$5/month while stopped. |
| **Terminate** | Pod is broken beyond repair, or you're done with VoxReach for weeks. Lose ~25 min on next bring-up. |

Default to **Stop**.
