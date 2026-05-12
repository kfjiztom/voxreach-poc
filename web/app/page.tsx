"use client";

import { BackstagePane } from "@/components/BackstagePane";
import { CallPane } from "@/components/CallPane";
import { Header } from "@/components/Header";
import { IframeCallPane } from "@/components/IframeCallPane";
import { useCallState } from "@/lib/useCallState";

// MOCK_MODE drives the demo from canned scenarios — used when no live audio backend is wired.
// Set NEXT_PUBLIC_MOCK_MODE=false on the pod to disable the mock scenario buttons in the
// regular CallPane. (Iframe mode below uses scenarios as backstage drivers regardless.)
const MOCK_MODE = (process.env.NEXT_PUBLIC_MOCK_MODE ?? "true") !== "false";

// PersonaPlex iframe URL — when set, the left pane embeds the live PersonaPlex UI.
// Leave unset for fully-mock dev mode. Example value:
//   https://abc123-8998.proxy.runpod.net
const PERSONAPLEX_URL = process.env.NEXT_PUBLIC_PERSONAPLEX_URL?.trim();

export default function HomePage() {
  const { state } = useCallState();

  return (
    <div className="flex min-h-screen flex-col">
      <Header />
      <main className="grid flex-1 grid-cols-1 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        {PERSONAPLEX_URL ? (
          <IframeCallPane state={state} iframeUrl={PERSONAPLEX_URL} />
        ) : (
          <CallPane state={state} mockMode={MOCK_MODE} />
        )}
        <BackstagePane state={state} />
      </main>
    </div>
  );
}
