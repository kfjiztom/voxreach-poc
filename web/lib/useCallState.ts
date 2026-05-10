"use client";

import { useEffect, useReducer, useRef } from "react";

import type { CallState, SidecarEvent } from "./types";
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
          return { ...state, order: ev.data.order };
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
  const lastChangesRef = useRef<string[]>([]);

  useEffect(() => {
    const es = new EventSource("/api/sidecar/events");

    const handle = (ev: MessageEvent, eventName: SidecarEvent["event"]) => {
      try {
        const parsed = JSON.parse(ev.data);
        // The python side emits {event, data}; pluck out data and re-tag with the event name.
        const innerData = parsed.data ?? parsed;
        dispatch({ type: "event", payload: { event: eventName, data: innerData } as SidecarEvent });
        if (eventName === "order_updated" && parsed.data?.changes) {
          lastChangesRef.current = parsed.data.changes;
        }
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
      "retrieval_hit",
      "latency_updated",
      "pos_write",
    ];
    for (const name of events) {
      es.addEventListener(name, (ev) => handle(ev as MessageEvent, name));
    }

    es.onerror = (err) => {
      console.warn("SSE connection error", err);
    };

    return () => {
      es.close();
    };
  }, []);

  return { state, dispatch, lastChanges: lastChangesRef.current };
}
