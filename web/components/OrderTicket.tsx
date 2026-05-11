"use client";

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
        </div>
      )}
    </div>
  );
}

function OrderItemRow({ item }: { item: OrderItem }) {
  const removed = item.status === "removed";
  const confirmed = item.status === "confirmed";

  const baseRow = "flex items-baseline justify-between transition-all duration-300";
  const stateClasses = removed
    ? "text-cream/30 line-through"
    : confirmed
    ? "text-cream"
    : "text-cream/70 italic";

  return (
    <li className={`animate-fade-in ${baseRow} ${stateClasses}`}>
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
    </li>
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
