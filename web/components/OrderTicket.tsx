"use client";

import type { OrderTicket as Order } from "@/lib/types";
import { formatCents } from "@/lib/types";

interface OrderTicketProps {
  order: Order;
}

export function OrderTicketView({ order }: OrderTicketProps) {
  const empty = order.items.length === 0;
  return (
    <div className="rounded-2xl bg-slate800/70 p-5">
      <div className="mb-3 flex items-center justify-between">
        <div className="flex items-center gap-2 text-xs uppercase tracking-widest text-cream/70">
          <span>📋</span>
          <span>Live order ticket</span>
        </div>
        <StatusPill status={order.status} />
      </div>

      {empty ? (
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
              {order.items.map((item, idx) => (
                <li key={idx} className="flex animate-fade-in items-baseline justify-between text-cream">
                  <span>
                    <span className="text-accentAmber">{item.quantity}×</span> {item.name}
                    {item.modifier ? <span className="text-cream/50"> ({item.modifier})</span> : null}
                  </span>
                  <span className="text-cream/80">{formatCents(item.line_total_cents)}</span>
                </li>
              ))}
            </ul>
            <div className="mt-3 border-t border-slate700 pt-2 text-right">
              <span className="text-[11px] uppercase tracking-widest text-cream/50">Subtotal: </span>
              <span className="text-base text-accentGreen">
                {formatCents(order.items.reduce((s, i) => s + i.line_total_cents, 0))}
              </span>
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
