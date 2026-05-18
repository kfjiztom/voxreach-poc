"use client";

import { useRef, useState } from "react";

import type { OrderItem, OrderTicket as Order, ItemStatus } from "@/lib/types";
import { activeItems, activeSubtotalCents, formatCents } from "@/lib/types";

interface OrderTicketProps {
  order: Order;
}

export function OrderTicketView({ order }: OrderTicketProps) {
  const allItems = order.items;
  const active = activeItems(order);
  const hasNothing = allItems.length === 0;

  return (
    <div className="rounded-2xl bg-slate800/70 p-5">
      <div className="mb-3 flex items-center justify-between">
        <div className="flex items-center gap-2 text-xs uppercase tracking-widest text-cream/70">
          <span>📋</span>
          <span>Live order ticket</span>
        </div>
        <StatusPill status={order.status} />
      </div>

      {hasNothing ? (
        <div className="rounded-xl border border-dashed border-slate700 px-4 py-8 text-center text-sm italic text-cream/40">
          Items will populate as Vox confirms them.
        </div>
      ) : (
        <div className="space-y-3 font-mono text-sm">
          <div className="text-cream/50">
            Customer: <span className="text-cream">{order.customer_name ?? "—"}</span>
            {order.customer_phone ? <span className="text-cream/40"> · {order.customer_phone}</span> : null}
          </div>
          <div className="rounded-xl border border-slate700 bg-slate900/70 p-3">
            <div className="mb-2 text-[10px] uppercase tracking-widest text-cream/40">Items</div>
            <ul className="space-y-1.5">
              {allItems.map((item, idx) => (
                <OrderItemRow key={`${item.name}-${idx}`} item={item} />
              ))}
            </ul>
            <div className="mt-3 border-t border-slate700 pt-2 text-right">
              <span className="text-[11px] uppercase tracking-widest text-cream/50">Subtotal: </span>
              <span className="text-base text-accentGreen">
                {formatCents(activeSubtotalCents(order))}
              </span>
              {active.length !== allItems.length && (
                <span className="ml-2 text-[10px] text-cream/40">
                  ({allItems.length - active.length} removed)
                </span>
              )}
            </div>
          </div>
          {order.pickup_time && (
            <div className="text-cream/70">
              Pickup: <span className="text-cream">{order.pickup_time}</span>
            </div>
          )}
          <ReadbackButton disabled={active.length === 0} />
        </div>
      )}
    </div>
  );
}

function ReadbackButton({ disabled }: { disabled: boolean }) {
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const [state, setState] = useState<"idle" | "loading" | "playing" | "error">("idle");
  const [errorMsg, setErrorMsg] = useState<string>("");
  const [transcript, setTranscript] = useState<string>("");

  async function onClick() {
    if (state === "playing" && audioRef.current) {
      audioRef.current.pause();
      audioRef.current.currentTime = 0;
      setState("idle");
      return;
    }
    setState("loading");
    setErrorMsg("");
    try {
      const r = await fetch("/api/sidecar/order/readback", { method: "POST" });
      if (!r.ok) {
        const detail = await r.text().catch(() => r.statusText);
        throw new Error(`HTTP ${r.status}: ${detail.slice(0, 120)}`);
      }
      const text = r.headers.get("X-Readback-Text") || "";
      setTranscript(text);
      const blob = await r.blob();
      const url = URL.createObjectURL(blob);
      const a = audioRef.current ?? new Audio();
      audioRef.current = a;
      a.src = url;
      a.onended = () => {
        setState("idle");
        URL.revokeObjectURL(url);
      };
      a.onerror = () => {
        setState("error");
        setErrorMsg("audio playback failed");
        URL.revokeObjectURL(url);
      };
      setState("playing");
      await a.play();
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e);
      setState("error");
      setErrorMsg(msg);
    }
  }

  const label =
    state === "loading"
      ? "Synthesizing..."
      : state === "playing"
      ? "■ Stop readback"
      : state === "error"
      ? "▶ Retry readback"
      : "▶ Play readback";

  return (
    <div className="flex flex-col gap-1">
      <button
        type="button"
        onClick={onClick}
        disabled={disabled || state === "loading"}
        className="self-start rounded-lg border border-accentCyan/30 bg-accentCyan/10 px-3 py-1.5 text-xs uppercase tracking-widest text-accentCyan transition-colors hover:bg-accentCyan/20 disabled:cursor-not-allowed disabled:opacity-40"
        title="Generates a TTS read-back of the current order so you (or the kitchen) can verify the ticket"
      >
        {label}
      </button>
      {transcript && state !== "error" ? (
        <div className="line-clamp-2 text-[10px] italic text-cream/40">
          “{transcript}”
        </div>
      ) : null}
      {state === "error" ? (
        <div className="text-[10px] text-accentRose">{errorMsg}</div>
      ) : null}
    </div>
  );
}

function OrderItemRow({ item }: { item: OrderItem }) {
  const removed = item.status === "removed";
  const confirmed = item.status === "confirmed";

  const stateClasses = removed
    ? "text-cream/30 line-through"
    : confirmed
    ? "text-cream"
    : "text-cream/70 italic";

  const hasDetail = Boolean(item.spice_level || item.notes);

  return (
    <li className="animate-fade-in">
      <div className={`flex items-baseline justify-between transition-all duration-300 ${stateClasses}`}>
        <span>
          <span className={removed ? "text-cream/30" : "text-accentAmber"}>
            {item.quantity}×
          </span>{" "}
          {item.name}
          {item.modifier ? (
            <span className={removed ? "text-cream/25" : "text-cream/50"}>
              {" "}({item.modifier})
            </span>
          ) : null}
          <ItemStatusBadge status={item.status} />
        </span>
        <span className={removed ? "text-cream/25" : "text-cream/80"}>
          {formatCents(item.line_total_cents)}
        </span>
      </div>
      {hasDetail && !removed ? (
        <div className="ml-7 mt-0.5 flex flex-wrap items-center gap-1.5 text-[10px]">
          {item.spice_level ? <SpiceChip level={item.spice_level} /> : null}
          {item.notes ? (
            <span className="rounded bg-slate700/60 px-1.5 py-0.5 italic text-cream/60">
              {item.notes}
            </span>
          ) : null}
        </div>
      ) : null}
    </li>
  );
}

function SpiceChip({ level }: { level: string }) {
  const normalized = level.toLowerCase();
  const isHot = /spic|hot/.test(normalized);
  const isMild = /mild|no spice|not spicy|no\s*spice/.test(normalized);
  const cls = isHot
    ? "bg-accentRose/20 text-accentRose"
    : isMild
    ? "bg-accentGreen/15 text-accentGreen"
    : "bg-accentAmber/20 text-accentAmber";
  return (
    <span className={`rounded px-1.5 py-0.5 uppercase tracking-widest ${cls}`}>
      spice · {level}
    </span>
  );
}

function ItemStatusBadge({ status }: { status: ItemStatus }) {
  if (status === "removed") {
    return (
      <span className="ml-2 rounded bg-accentRose/15 px-1.5 py-0.5 text-[9px] uppercase tracking-widest text-accentRose">
        cancelled
      </span>
    );
  }
  if (status === "pending") {
    return (
      <span className="ml-2 rounded bg-accentAmber/15 px-1.5 py-0.5 text-[9px] uppercase tracking-widest text-accentAmber">
        pending
      </span>
    );
  }
  return null; // "confirmed" — no badge, just clean text
}

function StatusPill({ status }: { status: Order["status"] }) {
  const map = {
    draft: { label: "draft", className: "bg-slate700 text-cream/70" },
    confirmed: { label: "confirmed", className: "bg-accentAmber/20 text-accentAmber" },
    written_to_pos: { label: "✓ written to Toast", className: "bg-accentGreen/20 text-accentGreen" },
  } as const;
  const it = map[status];
  return (
    <span className={`rounded-full px-2.5 py-0.5 text-[10px] uppercase tracking-widest ${it.className}`}>
      {it.label}
    </span>
  );
}
