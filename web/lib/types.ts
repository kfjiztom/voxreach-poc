// Shared types — must stay in sync with sidecar/schema.py

export type CallStatus = "idle" | "ringing" | "connected" | "ended";
export type Role = "customer" | "vox";
export type PosWriteStatus = "pending" | "writing" | "written" | "failed";

export interface TranscriptTurn {
  role: Role;
  text: string;
  timestamp: string;
  latency_ms: number | null;
}

export interface OrderItem {
  name: string;
  quantity: number;
  unit_price_cents: number;
  modifier: string | null;
  line_total_cents: number;
}

export interface OrderTicket {
  call_id: string;
  restaurant: string;
  customer_name: string | null;
  customer_phone: string | null;
  items: OrderItem[];
  pickup_time: string | null;
  notes: string | null;
  status: "draft" | "confirmed" | "written_to_pos";
  created_at: string;
}

export interface RetrievalHit {
  path: string;
  snippet: string;
  score: number | null;
  timestamp: string;
}

export interface LatencyMetric {
  first_audio_ms: number | null;
  last_turn_ms: number | null;
  avg_turn_ms: number | null;
  rag_hits: number;
  asr_confidence: number | null;
}

export interface CallState {
  call_id: string;
  status: CallStatus;
  started_at: string | null;
  ended_at: string | null;
  transcript: TranscriptTurn[];
  order: OrderTicket;
  retrieval_log: RetrievalHit[];
  latency: LatencyMetric;
  pos_write_status: PosWriteStatus;
}

export type SidecarEvent =
  | { event: "snapshot"; data: CallState }
  | { event: "call_started"; data: CallState }
  | { event: "call_ended"; data: CallState }
  | { event: "transcript_turn"; data: TranscriptTurn }
  | { event: "order_updated"; data: { order: OrderTicket; changes: string[] } }
  | { event: "retrieval_hit"; data: RetrievalHit }
  | { event: "latency_updated"; data: LatencyMetric }
  | {
      event: "pos_write";
      data:
        | { status: "writing" }
        | { status: "written"; response: Record<string, unknown>; order: OrderTicket }
        | { status: "failed"; error: string };
    };

export function emptyOrder(callId = ""): OrderTicket {
  return {
    call_id: callId,
    restaurant: "Hearth & Pass",
    customer_name: null,
    customer_phone: null,
    items: [],
    pickup_time: null,
    notes: null,
    status: "draft",
    created_at: new Date().toISOString(),
  };
}

export function emptyState(): CallState {
  return {
    call_id: "",
    status: "idle",
    started_at: null,
    ended_at: null,
    transcript: [],
    order: emptyOrder(),
    retrieval_log: [],
    latency: {
      first_audio_ms: null,
      last_turn_ms: null,
      avg_turn_ms: null,
      rag_hits: 0,
      asr_confidence: null,
    },
    pos_write_status: "pending",
  };
}

export function formatCents(cents: number): string {
  return `$${(cents / 100).toFixed(2)}`;
}
