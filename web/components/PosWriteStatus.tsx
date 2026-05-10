import type { PosWriteStatus } from "@/lib/types";

interface PosWriteStatusProps {
  status: PosWriteStatus;
}

export function PosWriteStatusView({ status }: PosWriteStatusProps) {
  const cfg = (() => {
    switch (status) {
      case "writing":
        return {
          label: "Writing to Toast…",
          className: "border-accentAmber/40 bg-accentAmber/10 text-accentAmber",
          icon: "⏳",
        };
      case "written":
        return {
          label: "✓ Written to Toast — fired to KDS-Line-1",
          className: "border-accentGreen/50 bg-accentGreen/10 text-accentGreen",
          icon: "✅",
        };
      case "failed":
        return {
          label: "POS write failed — escalated",
          className: "border-accentRose/50 bg-accentRose/10 text-accentRose",
          icon: "✕",
        };
      default:
        return {
          label: "POS write: pending call end",
          className: "border-slate700 bg-slate900/70 text-cream/50",
          icon: "💾",
        };
    }
  })();

  return (
    <div className={`rounded-2xl border ${cfg.className} p-4`}>
      <div className="flex items-center gap-3">
        <span className="text-xl">{cfg.icon}</span>
        <div className="flex-1">
          <div className="text-[10px] uppercase tracking-widest opacity-70">POS adapter</div>
          <div className="text-sm">{cfg.label}</div>
        </div>
      </div>
    </div>
  );
}
