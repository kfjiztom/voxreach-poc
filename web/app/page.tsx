"use client";

import { BackstagePane } from "@/components/BackstagePane";
import { CallPane } from "@/components/CallPane";
import { Header } from "@/components/Header";
import { IframeCallPane } from "@/components/IframeCallPane";
import { NativeCallPane } from "@/components/NativeCallPane";
import { useCallState } from "@/lib/useCallState";

const MOCK_MODE = (process.env.NEXT_PUBLIC_MOCK_MODE ?? "true") !== "false";

// Pane resolution priority (first non-empty wins):
//   1. NEXT_PUBLIC_MOSHI_WS_URL → NativeCallPane (Phase 1+, our own audio client)
//      e.g. ws://localhost:8998/api/chat
//   2. NEXT_PUBLIC_PERSONAPLEX_URL → IframeCallPane (legacy, embeds NVIDIA's stock UI)
//      e.g. http://localhost:8998
//   3. neither → CallPane (mock/scenario mode for offline frontend dev)
const MOSHI_WS_URL = process.env.NEXT_PUBLIC_MOSHI_WS_URL?.trim();
const PERSONAPLEX_URL = process.env.NEXT_PUBLIC_PERSONAPLEX_URL?.trim();

export default function HomePage() {
  const { state, dispatch, sseStatus, sseEventCount } = useCallState();
  const resetBackstage = () => dispatch({ type: "reset" });

  let leftPane: React.ReactNode;
  if (MOSHI_WS_URL) {
    leftPane = (
      <NativeCallPane state={state} moshiWsUrl={MOSHI_WS_URL} onResetBackstage={resetBackstage} />
    );
  } else if (PERSONAPLEX_URL) {
    leftPane = <IframeCallPane state={state} iframeUrl={PERSONAPLEX_URL} />;
  } else {
    leftPane = <CallPane state={state} mockMode={MOCK_MODE} />;
  }

  return (
    <div className="flex h-screen flex-col overflow-hidden">
      <Header />
      {/* Lock main to viewport remainder so each pane scrolls internally
          instead of the whole page growing past the window. The
          minmax(0,1fr) trick is what prevents grid blowout when child
          content is wider than the cell. */}
      <main className="grid min-h-0 flex-1 grid-cols-1 overflow-hidden lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        {leftPane}
        <BackstagePane
          state={state}
          onReset={resetBackstage}
          sseStatus={sseStatus}
          sseEventCount={sseEventCount}
        />
      </main>
    </div>
  );
}
