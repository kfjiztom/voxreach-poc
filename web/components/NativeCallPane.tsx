"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { useMoshiAudio } from "@/lib/useMoshiAudio";
import { useMoshiSession, type MoshiConnectionState } from "@/lib/useMoshiSession";
import type { CallState } from "@/lib/types";

import { CallControls } from "./CallControls";

interface NativeCallPaneProps {
  /** Backstage call state from the sidecar (transcript, order, etc.) */
  state: CallState;
  /** moshi.server WebSocket URL, e.g. ws://localhost:8998/api/chat */
  moshiWsUrl: string;
}

/**
 * Native call pane — direct WebSocket to moshi.server, no iframe.
 *
 * Phase 1 status:
 *   ✅ WebSocket lifecycle (connect, handshake, text stream, close)
 *   ✅ Vox-side typewriter transcript (from moshi text tokens, 0x02 frames)
 *   ⏳ Mic capture + opus encoding — stubbed (will be wired in Phase 1B)
 *   ⏳ Audio playback (opus decode + WebAudio) — stubbed (Phase 1B)
 *   ⏳ Audio stats panel (latency, missed audio) — stubbed
 *
 * Even without mic/playback wired, this pane proves out:
 *   - the WS connection succeeds
 *   - the persona override actually loads (we see Vox's greeting text stream)
 *   - the connection lifecycle is correct
 *
 * Phase 1B will add the audio worklets and wire up the actual voice loop.
 */
export function NativeCallPane({ state, moshiWsUrl }: NativeCallPaneProps) {
  const [voxText, setVoxText] = useState<string>("");
  const [showDebug, setShowDebug] = useState(false);

  // Per-frame counters live in refs — moshi sends ~12-15 frames/sec, and
  // calling setState on every frame triggers a render storm that freezes
  // the UI (button clicks stop registering). A 250ms interval copies
  // these refs into state so the debug panel still updates.
  const framesRxRef = useRef(0);
  const framesTxRef = useRef(0);
  const [framesDisplay, setFramesDisplay] = useState({ rx: 0, tx: 0 });
  useEffect(() => {
    const id = window.setInterval(() => {
      setFramesDisplay({ rx: framesRxRef.current, tx: framesTxRef.current });
    }, 250);
    return () => window.clearInterval(id);
  }, []);

  // Audio hook handles mic capture + opus encoding/decoding + playback.
  // sessionRef gives the audio hook access to the WS sender without
  // re-creating the recorder when the session object identity changes.
  const sessionRef = useRef<{ sendAudioFrame: (p: Uint8Array) => void } | null>(null);

  const audio = useMoshiAudio({
    onCapturePage: (page) => {
      sessionRef.current?.sendAudioFrame(page);
      framesTxRef.current += 1;
    },
  });

  const session = useMoshiSession({
    wsUrl: moshiWsUrl,
    onText: (token) => setVoxText((prev) => prev + token),
    onAudio: (oggPage) => {
      framesRxRef.current += 1;
      audio.pushOggPage(oggPage);
    },
    onHandshake: () => {
      setVoxText("");
      framesRxRef.current = 0;
      framesTxRef.current = 0;
      setFramesDisplay({ rx: 0, tx: 0 });
      // Mic capture starts AFTER handshake — moshi is ready to receive
      // and we don't want to waste mic frames during connection setup.
      void audio.start();
    },
    onError: () => {
      audio.stop();
    },
  });

  // Keep sessionRef in sync so the capture callback can send frames
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
    // We intentionally only react to session.state changes here
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session.state]);

  const onCall = useCallback(() => {
    if (session.state === "idle" || session.state === "closed" || session.state === "error") {
      session.start();
    } else {
      audio.stop();
      session.stop();
    }
  }, [session, audio]);

  // Reflect WS state in the same status-pill UI the rest of the app uses.
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

  return (
    <div className="flex h-full min-w-0 flex-col overflow-hidden bg-cream p-6 lg:p-10">
      <div className="mb-4 flex items-start justify-between">
        <div>
          <div className="font-serif text-3xl text-ink">Call Hearth &amp; Pass</div>
          <div className="mt-1 text-sm text-ink/60">
            (515) 555-0100 · Native audio · 123 Locust St, Des Moines IA
          </div>
        </div>
        <div className="flex items-center gap-3">
          <span className={`rounded-full px-3 py-1 text-[11px] uppercase tracking-widest ${sessionLabel.cls}`}>
            {sessionLabel.text}
          </span>
          {isActive && (
            <span className="font-mono text-sm text-ink/70">
              {String(Math.floor(elapsedSec / 60)).padStart(2, "0")}:
              {String(elapsedSec % 60).padStart(2, "0")}
            </span>
          )}
        </div>
      </div>

      {/* Main call surface */}
      <div className="mb-4 min-h-0 flex-1 rounded-3xl border border-clay/70 bg-white p-6 shadow-sm">
        <div className="flex h-full flex-col">
          {/* Idle / pre-call state */}
          {session.state === "idle" || session.state === "closed" || session.state === "error" ? (
            <div className="flex flex-1 flex-col items-center justify-center text-center">
              <div className="mb-4 text-5xl">📞</div>
              <div className="mb-2 font-serif text-2xl text-ink">
                Ready to call Vox
              </div>
              <div className="mb-6 max-w-md text-sm text-ink/60">
                {session.state === "error" && session.lastError
                  ? `Last attempt failed: ${session.lastError}`
                  : audio.state === "error" && audio.lastError
                  ? `Mic / audio setup failed: ${audio.lastError}`
                  : "Click below to start the call. Grant microphone access when prompted."}
              </div>
              <button
                type="button"
                onClick={onCall}
                className="rounded-full bg-moss px-6 py-3 text-sm font-semibold uppercase tracking-widest text-cream transition-colors hover:bg-moss/85"
              >
                ▶ Start call
              </button>
            </div>
          ) : (
            <>
              {/* Connecting / connected state — show transcript stream */}
              <div className="mb-3 text-xs uppercase tracking-widest text-ink/40">
                Vox says
              </div>
              <div className="min-h-[16rem] flex-1 overflow-y-auto rounded-2xl bg-cream/70 p-4 font-serif text-lg leading-relaxed text-ink">
                {voxText ? (
                  <span>{voxText}</span>
                ) : (
                  <span className="italic text-ink/30">
                    {session.state === "connecting" && "Connecting to Vox..."}
                    {session.state === "handshake" && "Loading persona..."}
                    {session.state === "connected" && "Waiting for Vox to greet you..."}
                  </span>
                )}
              </div>
            </>
          )}
        </div>
      </div>

      {/* Footer: call controls + debug toggle */}
      <div className="rounded-2xl border border-clay/70 bg-white/60 p-4">
        <div className="flex items-center justify-between">
          {isActive ? (
            <button
              type="button"
              onClick={onCall}
              className="rounded-full bg-persimmon px-5 py-2 text-xs font-semibold uppercase tracking-widest text-cream transition-colors hover:bg-persimmonDark"
            >
              ■ End call
            </button>
          ) : (
            <div className="text-xs text-ink/50">Backstage panel shows live order extraction →</div>
          )}
          <button
            type="button"
            onClick={() => setShowDebug((v) => !v)}
            className="text-[10px] uppercase tracking-widest text-ink/40 hover:text-ink/70"
          >
            {showDebug ? "Hide" : "Show"} debug
          </button>
        </div>

        {showDebug && (
          <div className="mt-3 grid grid-cols-2 gap-x-4 gap-y-1 font-mono text-[11px] text-ink/60">
            <div>WS state:</div>
            <div className="text-ink">{session.state}</div>
            <div>Audio state:</div>
            <div className="text-ink">{audio.state}</div>
            <div>WS URL:</div>
            <div className="truncate text-ink" title={moshiWsUrl}>{moshiWsUrl}</div>
            <div>Audio frames RX (server→us):</div>
            <div className="text-ink">{framesDisplay.rx}</div>
            <div>Audio frames TX (us→server):</div>
            <div className="text-ink">{framesDisplay.tx}</div>
            <div>Decoder pages in:</div>
            <div className="text-ink">{audio.decodeStats.pagesIn}</div>
            <div>Decoder ok:</div>
            <div className="text-ink">{audio.decodeStats.decodesOk}</div>
            <div>Decoder failed:</div>
            <div className={audio.decodeStats.decodesFailed > 0 ? "text-persimmon" : "text-ink"}>{audio.decodeStats.decodesFailed}</div>
            <div>Samples played:</div>
            <div className="text-ink">{audio.decodeStats.samplesPlayed.toLocaleString()}</div>
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
        )}
      </div>

      {/* Hidden — preserve the old CallControls integration so the backstage flow keeps working */}
      <div className="hidden">
        <CallControls status={state.status} mockMode={false} />
      </div>
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
