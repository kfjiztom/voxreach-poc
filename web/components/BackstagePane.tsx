import type { CallState } from "@/lib/types";

import { LatencyPanel } from "./LatencyPanel";
import { OrderTicketView } from "./OrderTicket";
import { PosWriteStatusView } from "./PosWriteStatus";
import { RetrievalLog } from "./RetrievalLog";

interface BackstagePaneProps {
  state: CallState;
}

export function BackstagePane({ state }: BackstagePaneProps) {
  return (
    <div className="flex h-full min-h-0 min-w-0 flex-col gap-3 overflow-y-auto overflow-x-hidden bg-slate950 p-4 text-cream lg:p-6">
      {/* Compact header — single row with title + badges */}
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-baseline gap-2 min-w-0">
          <span className="text-xs font-medium uppercase tracking-widest text-cream/80">
            Backstage
          </span>
          <span className="hidden text-[10px] text-cream/35 md:inline">demo-only</span>
        </div>
        <div className="flex items-center gap-2">
          {state.needs_human ? <NeedsHumanBadge reason={state.escalation_reason} /> : null}
          <div className="rounded-full border border-slate700 bg-slate900 px-2.5 py-0.5 font-mono text-[10px] text-cream/60">
            {state.call_id || "no call"}
          </div>
        </div>
      </div>

      <OrderTicketView order={state.order} />
      <RetrievalLog hits={state.retrieval_log} />
      <LatencyPanel latency={state.latency} />
      <PosWriteStatusView status={state.pos_write_status} />

      <div className="mt-auto pt-3 text-[10px] leading-relaxed text-cream/30">
        Stack: NVIDIA PersonaPlex 7B (moshi-based) · faster-whisper STT ·
        Ollama qwen2.5:3b extractor · Piper TTS readback · FastAPI sidecar.
        Open weights · self-hosted on Thunder Compute A100.
      </div>
    </div>
  );
}

function NeedsHumanBadge({ reason }: { reason: string | null | undefined }) {
  const label = reason && reason !== "general" ? `needs human · ${reason}` : "needs human";
  return (
    <span
      className="animate-fade-in rounded-full border border-accentRose/40 bg-accentRose/15 px-3 py-1 text-[10px] font-medium uppercase tracking-widest text-accentRose"
      title="Vox said an escalation phrase — operator should take over"
    >
      {label}
    </span>
  );
}
