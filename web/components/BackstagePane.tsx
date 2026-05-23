import type { SseStatus } from "@/lib/useCallState";
import type { CallState } from "@/lib/types";

import { LatencyPanel } from "./LatencyPanel";
import { OrderTicketView } from "./OrderTicket";
import { PosWriteStatusView } from "./PosWriteStatus";
import { RetrievalLog } from "./RetrievalLog";

interface BackstagePaneProps {
  state: CallState;
  /** Optional — when provided, renders a small "Reset" button in the header
   *  that clears the local UI state without touching the sidecar. Useful when
   *  a stale item from a previous call is lingering and you want a clean slate
   *  before the next call's SSE events arrive. */
  onReset?: () => void;
  /** Live SSE connection status — drives the dot indicator in the header.
   *  If "closed" or "error" on mobile, the backstage won't update at all
   *  even though the call audio works (the SSE connection got proxy-killed). */
  sseStatus?: SseStatus;
  sseEventCount?: number;
}

export function BackstagePane({ state, onReset, sseStatus, sseEventCount }: BackstagePaneProps) {
  return (
    <div className="flex h-full min-h-0 min-w-0 flex-col gap-3 overflow-y-auto overflow-x-hidden bg-slate950 p-4 text-cream lg:p-6">
      {/* Compact header — single row with title + badges */}
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 min-w-0">
          <SseDot status={sseStatus} count={sseEventCount} />
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
          {onReset ? (
            <button
              type="button"
              onClick={onReset}
              className="rounded-full border border-slate700 bg-slate900 px-2.5 py-0.5 font-mono text-[10px] text-cream/60 hover:bg-slate700 hover:text-cream"
              title="Clear backstage UI state (the next call will repopulate)"
            >
              reset
            </button>
          ) : null}
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

/**
 * Tiny status dot for the SSE connection — green when receiving events,
 * red when the proxy killed the long-lived connection. The event count
 * tooltip lets the user verify activity at a glance.
 */
function SseDot({ status, count }: { status: SseStatus | undefined; count: number | undefined }) {
  if (!status) return null;
  const cls =
    status === "open"
      ? "bg-accentGreen animate-pulse"
      : status === "connecting"
      ? "bg-accentAmber"
      : "bg-accentRose";
  const label = `SSE ${status}${count !== undefined ? ` · ${count} events` : ""}`;
  return <span className={`inline-block h-2 w-2 rounded-full ${cls}`} title={label} aria-label={label} />;
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
