# 90-second investor demo script

> Goal: in 90 seconds, prove that VoxReach (a) takes real phone calls, (b) writes structured orders to a POS, (c) runs on open weights you control. Three things the deck claims; the demo makes each one visible.

## Setup (before the room)

1. RunPod pod running (`runpod/start.sh`), tmux attached, all four services green.
2. Browser open on the demo URL, pointed at port 3001. Mic permissions granted.
3. Audio output going to a small Bluetooth speaker (not laptop speakers — sound quality matters here).
4. Backup: if pod is dead, switch the URL's `?mock=1` flag — same UI, canned conversation.

## Talk track (90s)

**Open (10s).** *"This is Hearth & Pass — a real Korean restaurant in Des Moines, Iowa. Right now if you call them at lunch they probably miss the call. Watch what happens when VoxReach answers."*

→ Click **Start call**. Wait for ringing-to-connected.

**Conversation (45s).** Speak naturally:

- *"Hi, can I place an order for pickup?"*
  → Vox: "Of course. What can I get started for you?"
- *"One bulgogi and one haemul pajeon."*
  → Vox confirms each item back, quotes subtotal.
  → **Point at the right pane.** *"Notice — the order is being structured in real time. Item, modifier, price. This is what would be written to Toast."*
- *"Six thirty for pickup, under Maya, 515-555-0182."*
  → Vox confirms full order back.
- *"Perfect, thanks!"*
  → Vox closes.

→ Click **End call**.

**Punch (25s).** *Watch the right pane*:

→ "Writing to Toast..." → green checkmark.

*"That JSON ticket on the right? In production that's an HTTP POST to Toast's Orders API. The kitchen display starts firing the order before the customer hangs up."*

*"The latency panel: 287 ms first audio, 412 ms average turn. That's what you get with a true full-duplex model — no STT-LLM-TTS pipeline."*

*"And the retrieval log — the model isn't hallucinating menu items. It's pulling them from Hearth & Pass's own data on every turn."*

**Close (10s).** *"All open weights. Self-hosted on a single A100. Same architecture I'll deploy in the pilot — this isn't a demo on rails."*

## Common questions and the answers under the hood

**Q: "Is this just OpenAI Realtime under the hood?"**
A: "No. The model is Kyutai's MoshiRAG, 7B parameters, open weights under CC-BY-4.0. Running on a $2/hour GPU. Slide to the backstage tab — that's the actual call ID, model name, and load source."

**Q: "What if it gets the order wrong?"**
A: "Two safeguards. First, Vox confirms the full ticket back to the customer before ending — you heard it just now. Second, escalation: if Vox can't understand something twice, it transfers. Want to see that? *(click Escalate scenario)*"

**Q: "How does this scale to a thousand restaurants?"**
A: "Per the infra plan in the data room, Phase B uses Modal memory snapshots for warm-pool scaling, Phase C self-hosts on AWS g5 spot fleets behind KEDA. This POC is the Phase A architecture — single-tenant on a hosted GPU. Clean migration path."

**Q: "Can I see the order JSON?"**
A: *(in another tab: `https://<pod>:8001/api/state`)*. "Full call state, structured. The same structure that goes to Toast."

## Time the room

If the conversation gets bogged down on a question, **stop the call and go to mock mode**. Don't fight a mic glitch in front of a check-writer — the canned scenarios are visually identical and preserve the punch.

## After the demo

- Drop the URL in the follow-up email so they can play with it themselves.
- Send the GitHub repo link if they ask.
- Note who asked which question — the *kind* of question is a signal of investor type.
