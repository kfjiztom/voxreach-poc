import type { LatencyMetric } from "@/lib/types";

interface LatencyPanelProps {
  latency: LatencyMetric;
}

export function LatencyPanel({ latency }: LatencyPanelProps) {
  return (
    <div className="rounded-2xl bg-slate800/70 p-5">
      <div className="mb-3 flex items-center gap-2 text-xs uppercase tracking-widest text-cream/70">
        <span>⚡</span>
        <span>Latency</span>
      </div>
      <dl className="grid grid-cols-1 gap-3 font-mono text-sm sm:grid-cols-3">
        <Stat label="First audio" value={fmt(latency.first_audio_ms)} unit="ms" tone="cyan" />
        <Stat label="Last turn" value={fmt(latency.last_turn_ms)} unit="ms" tone="amber" />
        <Stat label="Avg turn" value={fmt(latency.avg_turn_ms)} unit="ms" tone="green" />
      </dl>
      <div className="mt-3 flex items-center justify-between text-[11px] uppercase tracking-widest text-cream/40">
        <span>RAG hits</span>
        <span className="font-mono text-cream/70">{latency.rag_hits}</span>
      </div>
    </div>
  );
}

function fmt(v: number | null): string {
  return v === null ? "—" : v.toString();
}

function Stat({
  label,
  value,
  unit,
  tone,
}: {
  label: string;
  value: string;
  unit: string;
  tone: "cyan" | "amber" | "green";
}) {
  const color = {
    cyan: "text-accentCyan",
    amber: "text-accentAmber",
    green: "text-accentGreen",
  }[tone];
  return (
    <div className="rounded-xl border border-slate700 bg-slate900/70 px-3 py-2 text-center">
      <div className="text-[10px] uppercase tracking-widest text-cream/40">{label}</div>
      <div className={`mt-1 text-lg ${color}`}>
        {value}
        <span className="ml-0.5 text-[10px] text-cream/40">{unit}</span>
      </div>
    </div>
  );
}
