"use client";

import { useEffect, useState } from "react";

import type { CallState } from "@/lib/types";

import { CallControls } from "./CallControls";
import { TranscriptStream } from "./TranscriptStream";
import { Waveform } from "./Waveform";

interface CallPaneProps {
  state: CallState;
  mockMode: boolean;
}

export function CallPane({ state, mockMode }: CallPaneProps) {
  const isLive = state.status === "connected";
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    if (!state.started_at || state.status !== "connected") {
      setElapsed(0);
      return;
    }
    const start = new Date(state.started_at).getTime();
    const tick = () => setElapsed(Math.floor((Date.now() - start) / 1000));
    tick();
    const id = window.setInterval(tick, 1000);
    return () => window.clearInterval(id);
  }, [state.started_at, state.status]);

  const statusBadge = (() => {
    switch (state.status) {
      case "connected":
        return { label: "Connected", color: "bg-moss text-cream" };
      case "ended":
        return { label: "Call ended", color: "bg-ink/70 text-cream" };
      case "ringing":
        return { label: "Ringing", color: "bg-persimmon text-cream" };
      default:
        return { label: "Idle", color: "bg-clay text-ink/70" };
    }
  })();

  return (
    <div className="flex h-full flex-col bg-cream p-6 lg:p-10">
      <div className="mb-6 flex items-start justify-between">
        <div>
          <div className="font-serif text-3xl text-ink">Call Hearth &amp; Pass</div>
          <div className="mt-1 text-sm text-ink/60">
            (515) 555-0100 · 123 Locust St, Des Moines IA
          </div>
        </div>
        <div className="flex items-center gap-3">
          <span className={`rounded-full px-3 py-1 text-[11px] uppercase tracking-widest ${statusBadge.color}`}>
            {statusBadge.label}
          </span>
          {isLive && (
            <span className="font-mono text-sm text-ink/70">
              {String(Math.floor(elapsed / 60)).padStart(2, "0")}:
              {String(elapsed % 60).padStart(2, "0")}
            </span>
          )}
        </div>
      </div>

      <div className="mb-6 rounded-3xl border border-clay/70 bg-white/60 px-6 py-4">
        <Waveform active={isLive} />
      </div>

      <div className="mb-6 min-h-0 flex-1 rounded-3xl border border-clay/70 bg-white/40 p-4">
        <TranscriptStream turns={state.transcript} />
      </div>

      <CallControls status={state.status} mockMode={mockMode} />
    </div>
  );
}
