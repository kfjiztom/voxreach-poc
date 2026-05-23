/**
 * Custom Next.js server that ALSO proxies WebSocket connections to moshi.
 *
 * Why this exists:
 *   Cloud HTTPS reverse proxies (Thunder Compute, RunPod, Cloudflare) often
 *   only route ports they know about — typically just the "main" app port.
 *   The moshi server on port 8998 isn't reachable as wss://<id>-8998.* from
 *   the public internet on Thunder. But port 3001 (the Next.js app) IS
 *   reachable because that's the one users open.
 *
 *   By proxying the WebSocket through Next.js on port 3001, the browser only
 *   ever opens wss://<id>-3001.<proxy>/api/moshi-ws — same-origin, works
 *   anywhere the page itself works.
 *
 * Wire format passthrough:
 *   This is a raw byte tunnel — we don't parse or modify the moshi
 *   protocol. Browser bytes go to moshi, moshi bytes go back to the
 *   browser. Both sides think they're talking directly.
 */

import { createServer } from "node:http";
import { parse as parseUrl } from "node:url";
import next from "next";
import { WebSocketServer, WebSocket } from "ws";

const dev = process.env.NODE_ENV !== "production";
const hostname = process.env.HOSTNAME || "0.0.0.0";
const port = parseInt(process.env.PORT || "3001", 10);

// Where to forward WS connections from /api/moshi-ws — defaults to moshi
// on the same host. Override with MOSHI_WS_TARGET if moshi lives elsewhere.
const MOSHI_TARGET = process.env.MOSHI_WS_TARGET || "ws://localhost:8998";

const app = next({ dev, hostname, port });
const handle = app.getRequestHandler();

app.prepare().then(() => {
  const httpServer = createServer((req, res) => {
    const parsedUrl = parseUrl(req.url || "/", true);
    handle(req, res, parsedUrl).catch((err) => {
      console.error("[next-handler]", err);
      res.statusCode = 500;
      res.end("internal error");
    });
  });

  // WebSocket proxy attached to the same HTTP server. Browsers will
  // request /api/moshi-ws (any query string is preserved when forwarding).
  const wss = new WebSocketServer({ noServer: true });

  httpServer.on("upgrade", (req, socket, head) => {
    if (!req.url) {
      socket.destroy();
      return;
    }
    if (!req.url.startsWith("/api/moshi-ws")) {
      // Not our route — let Next.js handle (it has its own /_next/webpack-hmr ws)
      return;
    }
    wss.handleUpgrade(req, socket, head, (clientWs) => {
      // Strip /api/moshi-ws prefix; forward path + query to moshi as /api/chat...
      const url = new URL(req.url, "http://localhost");
      const queryString = url.search;
      const target = `${MOSHI_TARGET}/api/chat${queryString}`;

      const upstreamWs = new WebSocket(target);

      // Buffer client frames until upstream connects (browser may send
      // an Ogg header immediately on open; moshi's handshake takes ~1s)
      const pending = [];
      let upstreamReady = false;

      clientWs.on("message", (data, isBinary) => {
        if (!upstreamReady) {
          pending.push({ data, isBinary });
        } else if (upstreamWs.readyState === WebSocket.OPEN) {
          upstreamWs.send(data, { binary: isBinary });
        }
      });

      upstreamWs.on("open", () => {
        upstreamReady = true;
        for (const msg of pending) {
          upstreamWs.send(msg.data, { binary: msg.isBinary });
        }
        pending.length = 0;
      });

      upstreamWs.on("message", (data, isBinary) => {
        if (clientWs.readyState === WebSocket.OPEN) {
          clientWs.send(data, { binary: isBinary });
        }
      });

      const closeBoth = (code, reason) => {
        try { upstreamWs.close(code, reason); } catch {/* */}
        try { clientWs.close(code, reason); } catch {/* */}
      };

      clientWs.on("close", (code, reason) => closeBoth(code, reason?.toString()));
      upstreamWs.on("close", (code, reason) => closeBoth(code, reason?.toString()));
      clientWs.on("error", (err) => {
        console.warn("[moshi-ws-proxy] client error:", err.message);
        closeBoth(1011, "client error");
      });
      upstreamWs.on("error", (err) => {
        console.warn("[moshi-ws-proxy] upstream error:", err.message);
        closeBoth(1011, "upstream error");
      });
    });
  });

  httpServer.listen(port, () => {
    console.log(`> Ready on http://${hostname}:${port}`);
    console.log(`> WS proxy /api/moshi-ws → ${MOSHI_TARGET}/api/chat`);
  });
});
