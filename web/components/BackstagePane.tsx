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
    <div className="flex h-full flex-col gap-4 bg-slate950 p-6 text-cream lg:p-8">
      <div className="flex items-center justify-between">
        <div>
          <div className="text-sm font-medium uppercase tracking-widest text-cream/80">
            Backstage
          </div>
          <div className="text-[11px] text-cream/40">
            Visible to investors during demo · hidden from real callers in prod
          </div>
        </div>
        <div className="rounded-full border border-slate700 bg-slate900 px-3 py-1 font-mono text-[11px] text-cream/60">
          call_id: {state.call_id || "—"}
        </div>
      </div>

      <OrderTicketView order={state.order} />
      <RetrievalLog hits={state.retrieval_log} />
      <LatencyPanel latency={state.latency} />
      <PosWriteStatusView status={state.pos_write_status} />

      <div className="mt-auto pt-4 text-[10px] leading-relaxed text-cream/30">
        Stack: MoshiRAG (kyutai/moshika-rag-pytorch-bf16) · Kyutai STT · vLLM ·
        FastAPI sidecar. Open weights · CC-BY-4.0 · self-hosted on RunPod A100.
      </div>
    </div>
  );
}
