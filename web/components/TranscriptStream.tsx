"use client";

import { useEffect, useRef } from "react";

import type { TranscriptTurn } from "@/lib/types";

interface TranscriptStreamProps {
  turns: TranscriptTurn[];
}

export function TranscriptStream({ turns }: TranscriptStreamProps) {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [turns.length]);

  if (turns.length === 0) {
    return (
      <div className="flex h-full items-center justify-center text-sm italic text-ink/40">
        Conversation will appear here when the call begins.
      </div>
    );
  }

  return (
    <div className="scrollbar-light h-full space-y-3 overflow-y-auto pr-2">
      {turns.map((turn, idx) => (
        <TranscriptBubble key={`${idx}-${turn.timestamp}`} turn={turn} />
      ))}
      <div ref={bottomRef} />
    </div>
  );
}

function TranscriptBubble({ turn }: { turn: TranscriptTurn }) {
  const isVox = turn.role === "vox";
  return (
    <div className={`flex animate-fade-in ${isVox ? "justify-start" : "justify-end"}`}>
      <div className={`max-w-[80%] ${isVox ? "" : "text-right"}`}>
        <div
          className={`mb-1 text-[10px] uppercase tracking-wider ${
            isVox ? "text-persimmonDark" : "text-ink/50"
          }`}
        >
          {isVox ? "Vox" : "Customer"}
        </div>
        <div
          className={`rounded-2xl px-4 py-2.5 text-[15px] leading-snug ${
            isVox
              ? "bg-persimmon/10 text-ink"
              : "bg-ink text-cream"
          }`}
        >
          {turn.text}
        </div>
      </div>
    </div>
  );
}
