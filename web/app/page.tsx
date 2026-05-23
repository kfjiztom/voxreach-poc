"use client";

import { useEffect, useState } from "react";

import { BackstagePane } from "@/components/BackstagePane";
import { CallPane } from "@/components/CallPane";
import { Header } from "@/components/Header";
import { IframeCallPane } from "@/components/IframeCallPane";
import { NativeCallPane } from "@/components/NativeCallPane";
import { useCallState } from "@/lib/useCallState";

const MOCK_MODE = (process.env.NEXT_PUBLIC_MOCK_MODE ?? "true") !== "false";

// Pane resolution priority (first non-empty wins):
//   1. NEXT_PUBLIC_MOSHI_WS_URL → NativeCallPane (Phase 1+, our own audio client)
//   2. NEXT_PUBLIC_MOSHI_PORT → NativeCallPane, derive scheme + host from window
//      (useful for Thunder/Lambda where the page is served from
//      https://<id>-3001.proxy.tld and moshi lives at
//      wss://<id>-8998.proxy.tld with the same hostname pattern)
//   3. NEXT_PUBLIC_PERSONAPLEX_URL → IframeCallPane (legacy)
//   4. neither → CallPane (mock/scenario mode for offline dev)
const MOSHI_WS_URL_BUILD_TIME = process.env.NEXT_PUBLIC_MOSHI_WS_URL?.trim();
const MOSHI_PORT = process.env.NEXT_PUBLIC_MOSHI_PORT?.trim() ?? "8998";
const PERSONAPLEX_URL = process.env.NEXT_PUBLIC_PERSONAPLEX_URL?.trim();

/** Build the moshi WebSocket URL.
 *
 *  Default = same-origin proxy at /api/moshi-ws (handled by our custom
 *  Next.js server). This works through any HTTPS reverse proxy (Thunder,
 *  RunPod, Cloudflare) without needing the proxy to route additional
 *  ports — the browser only ever sees the same host/port the page was
 *  loaded from.
 *
 *  Overrides (in priority order):
 *   1. NEXT_PUBLIC_MOSHI_WS_URL — verbatim, full ws://... URL
 *   2. NEXT_PUBLIC_MOSHI_PORT — if set, bypass the proxy and connect
 *      directly to that port on the same host (only useful for local
 *      dev where the moshi port is reachable without proxying)
 *
 *  Returns undefined during SSR (no window) — page.tsx defers calling
 *  this until useEffect to avoid hydration mismatch.
 */
function resolveMoshiWsUrl(): string | undefined {
  if (MOSHI_WS_URL_BUILD_TIME) return MOSHI_WS_URL_BUILD_TIME;
  if (typeof window === "undefined") return undefined;
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  const host = window.location.host;

  // If NEXT_PUBLIC_MOSHI_PORT is set explicitly, use direct-port mode
  // (legacy / local dev). Strip any existing :port from the host and
  // append the requested one.
  if (process.env.NEXT_PUBLIC_MOSHI_PORT) {
    const portSwapped = host.replace(/-\d+(\.)/, `-${MOSHI_PORT}$1`);
    const finalHost = portSwapped !== host ? portSwapped : `${host.split(":")[0]}:${MOSHI_PORT}`;
    return `${proto}//${finalHost}/api/chat`;
  }

  // Default — same-origin proxy through our custom Next.js server.
  return `${proto}//${host}/api/moshi-ws`;
}

export default function HomePage() {
  const { state, dispatch, sseStatus, sseEventCount } = useCallState();
  const resetBackstage = () => dispatch({ type: "reset" });

  // Resolved AFTER mount via useEffect so SSR and the first client render
  // produce identical markup (avoids React hydration mismatch #418 —
  // resolveMoshiWsUrl reads window.location which doesn't exist during SSR).
  // Until the URL is set, the page renders in mock/fallback mode briefly
  // then swaps to NativeCallPane on the next render.
  const [moshiWsUrl, setMoshiWsUrl] = useState<string | undefined>(undefined);
  useEffect(() => {
    setMoshiWsUrl(resolveMoshiWsUrl());
  }, []);

  let leftPane: React.ReactNode;
  if (moshiWsUrl) {
    leftPane = (
      <NativeCallPane state={state} moshiWsUrl={moshiWsUrl} onResetBackstage={resetBackstage} />
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
