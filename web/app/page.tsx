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
 *  If NEXT_PUBLIC_MOSHI_WS_URL is set, use it verbatim — same build works for
 *  whatever environment you bake in.
 *  Otherwise derive from window.location: same host, ws/wss matching the
 *  page's scheme, port = NEXT_PUBLIC_MOSHI_PORT (default 8998). This lets
 *  ONE production build work for local dev AND Thunder/Lambda HTTPS proxy
 *  without env-var-at-build-time pitfalls.
 *
 *  Returns undefined during SSR (no window). Caller treats undefined as
 *  "no native pane configured" and falls back to iframe or mock pane.
 */
function resolveMoshiWsUrl(): string | undefined {
  if (MOSHI_WS_URL_BUILD_TIME) return MOSHI_WS_URL_BUILD_TIME;
  if (typeof window === "undefined") return undefined;
  // Same-host derivation. Thunder pattern: https://<id>-3001.thundercompute.net
  // → wss://<id>-3001.thundercompute.net  (but on port 8998).
  // For Thunder/Lambda the hostname encodes the port (<id>-<port>) so we
  // need to swap the port-in-hostname too. Pattern: <prefix>-<port>.<rest>
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  const host = window.location.host;
  const portSwapped = host.replace(/-\d+(\.)/, `-${MOSHI_PORT}$1`);
  // If the regex didn't match, the host has no -PORT- segment (plain
  // localhost or a bare domain). Use the port from MOSHI_PORT directly.
  const finalHost = portSwapped !== host ? portSwapped : `${host.split(":")[0]}:${MOSHI_PORT}`;
  return `${proto}//${finalHost}/api/chat`;
}

export default function HomePage() {
  const { state, dispatch, sseStatus, sseEventCount } = useCallState();
  const resetBackstage = () => dispatch({ type: "reset" });

  // Resolved at render time so window.location is available — same build
  // produces the right URL for local dev and Thunder HTTPS proxy alike.
  const moshiWsUrl = resolveMoshiWsUrl();

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
