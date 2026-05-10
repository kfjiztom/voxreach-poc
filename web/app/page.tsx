"use client";

import { BackstagePane } from "@/components/BackstagePane";
import { CallPane } from "@/components/CallPane";
import { Header } from "@/components/Header";
import { useCallState } from "@/lib/useCallState";

// MOCK_MODE drives the demo from canned scenarios — no GPU needed.
// Set NEXT_PUBLIC_MOCK_MODE=false on the RunPod box to wire up the real Moshi audio path.
const MOCK_MODE = (process.env.NEXT_PUBLIC_MOCK_MODE ?? "true") !== "false";

export default function HomePage() {
  const { state } = useCallState();

  return (
    <div className="flex min-h-screen flex-col">
      <Header />
      <main className="grid flex-1 grid-cols-1 lg:grid-cols-[3fr_2fr]">
        <CallPane state={state} mockMode={MOCK_MODE} />
        <BackstagePane state={state} />
      </main>
    </div>
  );
}
