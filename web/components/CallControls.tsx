"use client";

import { useState } from "react";

import { endCall, playMockScenario, startCall } from "@/lib/api";
import type { CallStatus } from "@/lib/types";

type ScenarioKey = "order" | "info" | "escalate" | "modify";

interface CallControlsProps {
  status: CallStatus;
  mockMode: boolean;
}

const SCENARIOS: Array<{ key: ScenarioKey; label: string; description: string }> = [
  { key: "order", label: "Place an order", description: "Bulgogi + pajeon → POS write" },
  { key: "modify", label: "Cancel & modify", description: "Caller changes mind mid-order" },
  { key: "info", label: "Ask info", description: "Hours, vegan options, parking" },
  { key: "escalate", label: "Escalate", description: "Caller asks for the manager" },
];

export function CallControls({ status, mockMode }: CallControlsProps) {
  const [busy, setBusy] = useState(false);
  const isLive = status === "connected";

  const handleStart = async () => {
    setBusy(true);
    try {
      await startCall();
    } catch (err) {
      console.error(err);
    } finally {
      setBusy(false);
    }
  };

  const handleEnd = async () => {
    setBusy(true);
    try {
      await endCall();
    } catch (err) {
      console.error(err);
    } finally {
      setBusy(false);
    }
  };

  const handleMock = async (scenario: ScenarioKey) => {
    setBusy(true);
    try {
      await playMockScenario(scenario);
    } catch (err) {
      console.error(err);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-4">
      {!mockMode && (
        <div className="flex items-center justify-center">
          {isLive ? (
            <button
              type="button"
              onClick={handleEnd}
              disabled={busy}
              className="rounded-full bg-ink px-8 py-3 text-sm font-medium uppercase tracking-widest text-cream shadow-sm transition hover:bg-ink/80 disabled:opacity-50"
            >
              End call
            </button>
          ) : (
            <button
              type="button"
              onClick={handleStart}
              disabled={busy}
              className="rounded-full bg-persimmon px-10 py-3 text-sm font-medium uppercase tracking-widest text-cream shadow-sm transition hover:bg-persimmonDark disabled:opacity-50"
            >
              Start call
            </button>
          )}
        </div>
      )}

      {mockMode && (
        <div>
          <div className="mb-2 text-center text-[10px] uppercase tracking-widest text-ink/40">
            Demo scenarios
          </div>
          <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
            {SCENARIOS.map((s) => (
              <button
                key={s.key}
                type="button"
                onClick={() => handleMock(s.key)}
                disabled={busy || isLive}
                className="rounded-xl border border-clay bg-cream px-3 py-2 text-left transition hover:border-persimmon hover:bg-persimmon/5 disabled:opacity-40"
              >
                <div className="text-sm font-medium text-ink">{s.label}</div>
                <div className="mt-0.5 text-[11px] text-ink/55">{s.description}</div>
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
