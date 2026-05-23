"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { useJingle } from "@/lib/useJingle";
import { useMoshiAudio } from "@/lib/useMoshiAudio";
import { useMoshiSession, type MoshiConnectionState } from "@/lib/useMoshiSession";
import {
  PERSONAPLEX_VOICES,
  DEFAULT_VOICE_FILE,
  loadStoredVoice,
  storeVoice,
} from "@/lib/personaplexVoices";
import type { CallState } from "@/lib/types";

import { CallControls } from "./CallControls";

interface NativeCallPaneProps {
  /** Backstage call state from the sidecar (transcript, order, etc.) */
  state: CallState;
  /** moshi.server WebSocket URL, e.g. ws://localhost:8998/api/chat */
  moshiWsUrl: string;
  /** Optional — when provided, clears the backstage UI state on every new call.
   *  Belt-and-suspenders: the sidecar also emits call_started which the reducer
   *  picks up, but doing this client-side too eliminates any visible stale-state
   *  flash while the SSE event is in flight. */
  onResetBackstage?: () => void;
}

/**
 * Native call pane — direct WebSocket to moshi.server, no iframe.
 *
 * Layout is intentionally tight so the right-hand BackstagePane gets visual
 * priority: the call pane is just "phone in your hand", the order ticket is
 * "what the kitchen sees". Compact header, prominent activity ring, scrollable
 * transcript that's clearly bounded.
 */
export function NativeCallPane({ state, moshiWsUrl, onResetBackstage }: NativeCallPaneProps) {
  const [voxText, setVoxText] = useState<string>("");
  const [showDebug, setShowDebug] = useState(false);
  const [voice, setVoice] = useState<string>(DEFAULT_VOICE_FILE);
  // Restore the operator's last-used voice on mount
  useEffect(() => {
    setVoice(loadStoredVoice());
  }, []);
  const onVoiceChange = (file: string) => {
    setVoice(file);
    storeVoice(file);
  };

  // Per-frame counters live in refs — moshi sends ~12-15 frames/sec, and
  // calling setState on every frame triggers a render storm that freezes
  // the UI. A 250ms interval copies these refs into state for display.
  const framesRxRef = useRef(0);
  const framesTxRef = useRef(0);
  const [framesDisplay, setFramesDisplay] = useState({ rx: 0, tx: 0 });
  // Activity pulse — when TX/RX deltas are non-zero we know mic/voice
  // are active. Used to drive the visual mic/voice indicators.
  const lastFramesRef = useRef({ rx: 0, tx: 0 });
  const [activity, setActivity] = useState<{ micActive: boolean; voxActive: boolean }>({
    micActive: false,
    voxActive: false,
  });
  useEffect(() => {
    const id = window.setInterval(() => {
      const rx = framesRxRef.current;
      const tx = framesTxRef.current;
      const micActive = tx > lastFramesRef.current.tx;
      const voxActive = rx > lastFramesRef.current.rx;
      lastFramesRef.current = { rx, tx };
      setFramesDisplay({ rx, tx });
      setActivity({ micActive, voxActive });
    }, 250);
    return () => window.clearInterval(id);
  }, []);

  // Audio hook handles mic capture + opus encoding/decoding + playback.
  const sessionRef = useRef<{ sendAudioFrame: (p: Uint8Array) => void } | null>(null);

  const audio = useMoshiAudio({
    onCapturePage: (page) => {
      sessionRef.current?.sendAudioFrame(page);
      framesTxRef.current += 1;
    },
  });

  const session = useMoshiSession({
    wsUrl: moshiWsUrl,
    voicePrompt: voice,
    onText: (token) => setVoxText((prev) => prev + token),
    onAudio: (oggPage) => {
      framesRxRef.current += 1;
      audio.pushOggPage(oggPage);
    },
    onHandshake: () => {
      setVoxText("");
      framesRxRef.current = 0;
      framesTxRef.current = 0;
      lastFramesRef.current = { rx: 0, tx: 0 };
      setFramesDisplay({ rx: 0, tx: 0 });
      void audio.start();
    },
    onError: () => {
      audio.stop();
    },
  });

  sessionRef.current = { sendAudioFrame: session.sendAudioFrame };

  // Stop audio whenever the WS leaves the active states
  useEffect(() => {
    if (
      session.state === "closed" ||
      session.state === "error" ||
      session.state === "idle"
    ) {
      audio.stop();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session.state]);

  const jingle = useJingle();
  const onCall = useCallback(() => {
    if (session.state === "idle" || session.state === "closed" || session.state === "error") {
      // Clear the backstage UI before opening the new WS so any leftover
      // items / transcript from the previous call disappear immediately.
      onResetBackstage?.();
      // Brief synthesized chime — gives the caller audible feedback that
      // the call is connecting while moshi loads (~1-3s gap before Vox
      // greets). Plays once and fades out before Vox's voice arrives.
      jingle.play();
      session.start();
    } else {
      audio.stop();
      session.stop();
    }
  }, [session, audio, onResetBackstage, jingle]);

  const sessionLabel = labelForSession(session.state);

  // Elapsed timer
  const startedAtRef = useRef<number | null>(null);
  const [elapsedSec, setElapsedSec] = useState(0);
  useEffect(() => {
    if (session.state === "connected" && startedAtRef.current === null) {
      startedAtRef.current = Date.now();
    } else if (session.state === "idle" || session.state === "closed" || session.state === "error") {
      startedAtRef.current = null;
      setElapsedSec(0);
    }
    if (startedAtRef.current === null) return;
    const tick = () => {
      if (startedAtRef.current === null) return;
      setElapsedSec(Math.floor((Date.now() - startedAtRef.current) / 1000));
    };
    tick();
    const id = window.setInterval(tick, 1000);
    return () => window.clearInterval(id);
  }, [session.state]);

  const isActive = session.state === "connecting" || session.state === "handshake" || session.state === "connected";
  const isIdle = session.state === "idle" || session.state === "closed" || session.state === "error";

  return (
    <div className="flex h-full min-w-0 flex-col overflow-hidden bg-cream p-4 lg:p-6">
      {/* Compact header strip */}
      <div className="mb-3 flex items-center justify-between">
        <div className="min-w-0">
          <div className="font-serif text-xl text-ink leading-tight">Call Hearth &amp; Pass</div>
          <div className="text-[11px] text-ink/55">(515) 555-0100 · Native audio</div>
        </div>
        <div className="flex items-center gap-2">
          <span className={`rounded-full px-2.5 py-0.5 text-[10px] uppercase tracking-widest ${sessionLabel.cls}`}>
            {sessionLabel.text}
          </span>
          {isActive && (
            <span className="font-mono text-xs text-ink/70 tabular-nums">
              {String(Math.floor(elapsedSec / 60)).padStart(2, "0")}:
              {String(elapsedSec % 60).padStart(2, "0")}
            </span>
          )}
        </div>
      </div>

      {/* Active-call activity strip — compact, replaces the big phone emoji surface */}
      {isActive ? (
        <div className="mb-3 flex items-center justify-between rounded-2xl border border-clay/60 bg-white px-4 py-3 shadow-sm">
          <div className="flex items-center gap-4">
            <ActivityRing micActive={activity.micActive} voxActive={activity.voxActive} state={session.state} />
            <div className="min-w-0">
              <div className="text-[10px] uppercase tracking-widest text-ink/40">
                {session.state === "connecting" && "Opening line"}
                {session.state === "handshake" && "Loading persona"}
                {session.state === "connected" && (activity.voxActive ? "Vox speaking" : activity.micActive ? "Listening" : "Connected")}
              </div>
              <div className="text-sm text-ink/80 leading-tight">
                {audio.state === "running" ? "Mic live · Audio out OK" : audio.state === "starting" ? "Initializing mic…" : "Audio paused"}
                <span className="ml-2 font-mono text-[10px] text-ink/40">voice: {voice.replace(".pt", "")}</span>
              </div>
            </div>
          </div>
          <button
            type="button"
            onClick={onCall}
            className="rounded-full bg-persimmon px-4 py-1.5 text-[11px] font-semibold uppercase tracking-widest text-cream transition-colors hover:bg-persimmonDark"
          >
            ■ End call
          </button>
        </div>
      ) : (
        // Idle / pre-call — single tight card with the call button prominent
        <div className="mb-3 rounded-2xl border border-clay/60 bg-white px-4 py-3 shadow-sm">
          <div className="flex items-center justify-between gap-3">
            <div className="min-w-0">
              <div className="text-[10px] uppercase tracking-widest text-ink/40">
                {session.state === "error" ? "Last call ended in error" : session.state === "closed" ? "Call ended" : "Ready"}
              </div>
              <div className="text-sm text-ink/80 leading-tight">
                {session.state === "error" && session.lastError
                  ? session.lastError
                  : audio.state === "error" && audio.lastError
                  ? `Mic / audio: ${audio.lastError}`
                  : "Click to call Vox. Grant microphone access when prompted."}
              </div>
            </div>
            <button
              type="button"
              onClick={onCall}
              className="shrink-0 rounded-full bg-moss px-5 py-2 text-xs font-semibold uppercase tracking-widest text-cream transition-colors hover:bg-moss/85"
            >
              ▶ Start call
            </button>
          </div>
          {/* Voice picker — only meaningful while idle, applies to next call */}
          <div className="mt-3 flex items-center gap-2 border-t border-clay/30 pt-3">
            <label htmlFor="voice-select" className="text-[10px] uppercase tracking-widest text-ink/40">
              Voice
            </label>
            <select
              id="voice-select"
              value={voice}
              onChange={(e) => onVoiceChange(e.target.value)}
              className="flex-1 rounded-lg border border-clay/50 bg-cream/40 px-2 py-1 font-mono text-xs text-ink focus:border-moss focus:outline-none"
            >
              <optgroup label="Natural · Female">
                {PERSONAPLEX_VOICES.filter((v) => v.category === "natural-female").map((v) => (
                  <option key={v.file} value={v.file}>{v.label}</option>
                ))}
              </optgroup>
              <optgroup label="Natural · Male">
                {PERSONAPLEX_VOICES.filter((v) => v.category === "natural-male").map((v) => (
                  <option key={v.file} value={v.file}>{v.label}</option>
                ))}
              </optgroup>
              <optgroup label="Variety · Female">
                {PERSONAPLEX_VOICES.filter((v) => v.category === "variety-female").map((v) => (
                  <option key={v.file} value={v.file}>{v.label}</option>
                ))}
              </optgroup>
              <optgroup label="Variety · Male">
                {PERSONAPLEX_VOICES.filter((v) => v.category === "variety-male").map((v) => (
                  <option key={v.file} value={v.file}>{v.label}</option>
                ))}
              </optgroup>
            </select>
          </div>
        </div>
      )}

      {/* Transcript — bounded scroll so it doesn't take over the pane */}
      <div className="mb-3 min-h-0 flex-1 overflow-hidden rounded-2xl border border-clay/60 bg-white shadow-sm">
        <div className="border-b border-clay/30 px-4 py-2 text-[10px] uppercase tracking-widest text-ink/40">
          Vox transcript
        </div>
        <div className="h-full overflow-y-auto p-4 font-serif text-base leading-relaxed text-ink">
          {voxText ? (
            <span>{voxText}</span>
          ) : (
            <span className="italic text-ink/30">
              {isIdle && "Transcript will appear here when you call."}
              {session.state === "connecting" && "Connecting to Vox…"}
              {session.state === "handshake" && "Loading persona…"}
              {session.state === "connected" && "Waiting for Vox to greet you…"}
            </span>
          )}
        </div>
      </div>

      {/* Footer: debug toggle only — call button is now in the activity strip above */}
      <div className="flex items-center justify-between text-[10px] uppercase tracking-widest text-ink/40">
        <span>Backstage panel shows live order extraction →</span>
        <button
          type="button"
          onClick={() => setShowDebug((v) => !v)}
          className="hover:text-ink/70"
        >
          {showDebug ? "Hide" : "Show"} debug
        </button>
      </div>

      {showDebug && (
        <div className="mt-2 rounded-xl border border-clay/60 bg-white/80 p-3">
          <div className="grid grid-cols-2 gap-x-4 gap-y-0.5 font-mono text-[10px] text-ink/60">
            <div>WS state:</div>
            <div className="text-ink">{session.state}</div>
            <div>Audio state:</div>
            <div className="text-ink">{audio.state}</div>
            <div>WS URL:</div>
            <div className="truncate text-ink" title={moshiWsUrl}>{moshiWsUrl}</div>
            <div>Frames RX / TX:</div>
            <div className="text-ink">{framesDisplay.rx} / {framesDisplay.tx}</div>
            <div>Decoder ok / failed:</div>
            <div className={audio.decodeStats.decodesFailed > 0 ? "text-persimmon" : "text-ink"}>
              {audio.decodeStats.decodesOk} / {audio.decodeStats.decodesFailed}
            </div>
            <div>Samples played:</div>
            <div className="text-ink">{audio.decodeStats.samplesPlayed.toLocaleString()}</div>
            <div>Frames dropped (lag cap):</div>
            <div className={audio.decodeStats.framesDroppedForLag > 0 ? "text-persimmon" : "text-ink"}>
              {audio.decodeStats.framesDroppedForLag}
            </div>
            <div>Playback lead:</div>
            <div className={audio.decodeStats.playbackLeadMs > 200 ? "text-persimmon" : "text-ink"}>
              {audio.decodeStats.playbackLeadMs} ms
            </div>
            <div>Vox text chars:</div>
            <div className="text-ink">{voxText.length}</div>
            <div>Backstage call ID:</div>
            <div className="truncate text-ink" title={state.call_id}>{state.call_id || "—"}</div>
            {session.lastError && (
              <>
                <div>WS error:</div>
                <div className="text-persimmon">{session.lastError}</div>
              </>
            )}
            {audio.lastError && (
              <>
                <div>Audio error:</div>
                <div className="text-persimmon">{audio.lastError}</div>
              </>
            )}
          </div>
        </div>
      )}

      {/* Hidden — preserve the old CallControls integration so the backstage flow keeps working */}
      <div className="hidden">
        <CallControls status={state.status} mockMode={false} />
      </div>
    </div>
  );
}

/**
 * Compact ring with two pulse layers — one for the caller's mic, one for
 * Vox's voice. Animates when activity is detected on either side.
 */
function ActivityRing({
  micActive,
  voxActive,
  state,
}: {
  micActive: boolean;
  voxActive: boolean;
  state: MoshiConnectionState;
}) {
  const isWaiting = state === "connecting" || state === "handshake";
  return (
    <div className="relative h-12 w-12 shrink-0">
      {/* Outer ring — pulses when Vox is speaking */}
      <div
        className={`absolute inset-0 rounded-full border-2 transition-all duration-200 ${
          voxActive ? "border-moss animate-ping" : "border-moss/30"
        }`}
      />
      {/* Inner dot — fills when mic is active */}
      <div
        className={`absolute inset-2 rounded-full transition-all duration-200 ${
          isWaiting
            ? "bg-persimmon/60 animate-pulse"
            : micActive
            ? "bg-persimmon scale-110"
            : "bg-moss"
        }`}
      />
    </div>
  );
}

function labelForSession(s: MoshiConnectionState): { text: string; cls: string } {
  switch (s) {
    case "idle":
      return { text: "Idle", cls: "bg-clay text-ink/70" };
    case "connecting":
      return { text: "Connecting", cls: "bg-persimmon text-cream" };
    case "handshake":
      return { text: "Loading", cls: "bg-persimmon text-cream" };
    case "connected":
      return { text: "Connected", cls: "bg-moss text-cream" };
    case "closing":
      return { text: "Ending", cls: "bg-ink/40 text-cream" };
    case "closed":
      return { text: "Ended", cls: "bg-ink/70 text-cream" };
    case "error":
      return { text: "Error", cls: "bg-persimmonDark text-cream" };
  }
}
