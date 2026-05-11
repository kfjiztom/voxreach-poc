"use client";

import type { CallState } from "@/lib/types";

import { CallControls } from "./CallControls";

interface IframeCallPaneProps {
  state: CallState;
  iframeUrl: string;
}

export function IframeCallPane({ state, iframeUrl }: IframeCallPaneProps) {
  return (
    <div className="flex h-full flex-col bg-cream p-6 lg:p-10">
      <div className="mb-4 flex items-start justify-between">
        <div>
          <div className="font-serif text-3xl text-ink">Call Hearth &amp; Pass</div>
          <div className="mt-1 text-sm text-ink/60">
            (515) 555-0100 · Live PersonaPlex audio · 123 Locust St, Des Moines IA
          </div>
        </div>
        <div className="flex items-center gap-3">
          <span className="rounded-full bg-moss px-3 py-1 text-[11px] uppercase tracking-widest text-cream">
            Live audio
          </span>
        </div>
      </div>

      <div className="mb-4 min-h-0 flex-1 overflow-hidden rounded-3xl border border-clay/70 bg-white shadow-sm">
        <iframe
          src={iframeUrl}
          allow="microphone; autoplay; clipboard-write"
          allowFullScreen
          className="h-full w-full border-0"
          title="PersonaPlex live audio"
        />
      </div>

      <div className="rounded-2xl border border-clay/70 bg-white/60 p-4">
        <div className="mb-2 text-center text-[10px] uppercase tracking-widest text-ink/40">
          Drive the backstage panel while you talk above &nbsp;→
        </div>
        <CallControls status={state.status} mockMode={true} />
        <div className="mt-3 text-center text-[10px] text-ink/35">
          Live audio above is real PersonaPlex. Backstage panel on the right shows the demo of
          our productized order-capture + POS-write flow (mock data — real wiring lands in
          Phase B).
        </div>
      </div>

      <noscript>
        <div className="mt-2 rounded-lg border border-persimmon/30 bg-persimmon/5 p-3 text-sm text-persimmonDark">
          The live audio requires JavaScript. Open{" "}
          <a className="underline" href={iframeUrl}>
            the PersonaPlex UI directly
          </a>{" "}
          if the pane above is blank.
        </div>
      </noscript>
    </div>
  );
}
