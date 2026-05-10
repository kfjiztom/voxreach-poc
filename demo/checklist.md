# Pre-demo checklist

Run through this 30 minutes before any live investor or pilot demo.

## GPU pod
- [ ] RunPod pod is **on-demand** (not spot) for the demo window
- [ ] `tmux attach -t voxreach` shows all 4 windows healthy:
  - [ ] vllm — last log line is "Application startup complete"
  - [ ] moshi — last log line is "Listening on 0.0.0.0:8998"
  - [ ] sidecar — last log line is "Uvicorn running on http://0.0.0.0:8001"
  - [ ] web — last log line shows "ready in" and a port
- [ ] `curl https://<pod>-8001.proxy.runpod.net/api/state` returns `null` (no stale call)
- [ ] Pod has > 4 hours of credit on the account

## Audio path
- [ ] Bluetooth speaker paired and selected as system output
- [ ] System volume at 60–70%
- [ ] Mic permission granted to the demo URL in Chrome (check `chrome://settings/content/microphone`)
- [ ] Quick mic test — speak into mic, see waveform animate on the demo page

## Network
- [ ] Hardwired ethernet if available (Wi-Fi has packet loss that ruins audio)
- [ ] If wireless: confirm 5 GHz band, signal > -65 dBm
- [ ] Bandwidth test: > 10 Mbps up/down (`fast.com`)
- [ ] Disable VPN — RunPod proxy URL latency doubles through most VPNs

## Browser
- [ ] Chrome (Safari has WebRTC quirks with self-signed RunPod certs)
- [ ] Demo tab pinned, sidecar `/api/state` tab in adjacent window for the "show me the JSON" moment
- [ ] Close Slack, email, and notification-noisy apps. **DO NOT screen-share with notifications on.**
- [ ] Cmd-click "Start call" once before the demo to warm up the mic permission prompt

## Demo data
- [ ] Mock fallback works — click each of the 3 scenario buttons, confirm full flow plays
- [ ] `https://<pod>-8001.proxy.runpod.net/api/state` responds (fallback debug view)
- [ ] If demoing a specific restaurant, double-check the persona file matches what the investor will hear (right name, right city)

## Recording
- [ ] If demo is being recorded: `ffmpeg` or QuickTime ready
- [ ] Practice run completed within the last 24 hours
- [ ] Have a 90-second screen-recording of a successful run on hand as a fallback

## Comms
- [ ] Phone on silent
- [ ] Slack on Do Not Disturb
- [ ] Calendar blocked for 30 min after the slot — investor demos run over

## Kill switch
- [ ] Know the tmux command to kill the call without ending the pod (`tmux send-keys -t voxreach:moshi C-c`)
- [ ] Know how to start the mock scenario from the URL bar if the live model fails mid-call

---

If any item is unchecked at T-5 min, **switch to mock mode** and don't apologize for it. The mock demo is visually identical, preserves the story, and saves you from debugging in front of an audience.
