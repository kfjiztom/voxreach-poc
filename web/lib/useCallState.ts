"use client";

import { useEffect, useReducer } from "react";

import type { CallState, OrderItem, SidecarEvent } from "./types";
import { emptyState } from "./types";

type Action =
  | { type: "snapshot"; payload: CallState }
  | { type: "event"; payload: SidecarEvent }
  | { type: "reset" };

function reducer(state: CallState, action: Action): CallState {
  switch (action.type) {
    case "snapshot":
      return action.payload;
    case "reset":
      return emptyState();
    case "event": {
      const ev = action.payload;
      switch (ev.event) {
        case "snapshot":
        case "call_started":
        case "call_ended":
          return ev.data;

        case "transcript_turn":
          return { ...state, transcript: [...state.transcript, ev.data] };

        case "order_updated":
          // Authoritative snapshot of the whole order from the sidecar
          return { ...state, order: ev.data.order };

        case "item_added": {
          const newItem = ev.data;
          // Avoid duplicates if order_updated already arrived for this item
          if (state.order.items.some((i) => i.name === newItem.name && i.status !== "removed")) {
            return state;
          }
          return {
            ...state,
            order: { ...state.order, items: [...state.order.items, newItem] },
          };
        }

        case "item_removed":
          return {
            ...state,
            order: {
              ...state.order,
              items: state.order.items.map((i) =>
                i.name === ev.data.name ? { ...i, status: "removed" as const } : i,
              ),
            },
          };

        case "item_modified":
          return {
            ...state,
            order: {
              ...state.order,
              items: state.order.items.map((i): OrderItem => {
                if (i.name !== ev.data.name || i.status === "removed") return i;
                if (ev.data.field === "quantity") {
                  const newQty = Number(ev.data.new) || i.quantity;
                  return {
                    ...i,
                    quantity: newQty,
                    line_total_cents: i.unit_price_cents * newQty,
                  };
                }
                if (ev.data.field === "modifier") {
                  return { ...i, modifier: (ev.data.new as string | null) ?? null };
                }
                return i;
              }),
            },
          };

        case "item_confirmed":
          return {
            ...state,
            order: {
              ...state.order,
              items: state.order.items.map((i): OrderItem =>
                i.name === ev.data.name && i.status === "pending"
                  ? { ...i, status: "confirmed" as const }
                  : i,
              ),
            },
          };

        case "retrieval_hit":
          return {
            ...state,
            retrieval_log: [...state.retrieval_log, ev.data],
          };

        case "latency_updated":
          return { ...state, latency: ev.data };

        case "pos_write":
          if (ev.data.status === "written") {
            return {
              ...state,
              pos_write_status: "written",
              order: ev.data.order,
            };
          }
          return {
            ...state,
            pos_write_status: ev.data.status,
          };

        default:
          return state;
      }
    }
    default:
      return state;
  }
}

export function useCallState() {
  const [state, dispatch] = useReducer(reducer, emptyState());

  useEffect(() => {
    const es = new EventSource("/api/sidecar/events");

    const handle = (ev: MessageEvent, eventName: SidecarEvent["event"]) => {
      try {
        const parsed = JSON.parse(ev.data);
        // Sidecar emits {event, data}; pluck out data, re-tag with event name
        const innerData = parsed.data ?? parsed;
        dispatch({
          type: "event",
          payload: { event: eventName, data: innerData } as SidecarEvent,
        });
      } catch (err) {
        console.error("SSE parse error", err, ev.data);
      }
    };

    const events: SidecarEvent["event"][] = [
      "snapshot",
      "call_started",
      "call_ended",
      "transcript_turn",
      "order_updated",
      "item_added",
      "item_removed",
      "item_modified",
      "item_confirmed",
      "retrieval_hit",
      "latency_updated",
      "pos_write",
    ];
    for (const name of events) {
      es.addEventListener(name, (ev) => handle(ev as MessageEvent, name));
    }
    // Heartbeat from the server's HTTP/2-keepalive flush — silently absorb,
    // never dispatch (avoids "unknown event 'ready'" console noise).
    es.addEventListener("ready", () => {});

    es.onerror = (err) => {
      console.warn("SSE connection error", err);
    };

    return () => {
      es.close();
    };
  }, []);

  return { state, dispatch };
}
