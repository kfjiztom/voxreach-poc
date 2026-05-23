"use client";

import { useCallback, useEffect, useRef, useState } from "react";

/**
 * Direct WebSocket session manager for moshi.server — no iframe.
 *
 * Wire protocol (from moshi.server source + observed client traffic):
 *   - Open WS to ws://host/api/chat?text_prompt=...&voice_prompt=NATF0.pt&...
 *   - Server sends handshake byte 0x00 once system prompts are loaded.
 *   - Then duplex Ogg/Opus pages:
 *       - Client → server: Ogg/Opus pages containing mic PCM @ 24 kHz mono
 *       - Server → client: prefix-tagged binary frames:
 *           0x01 + Ogg page  → Vox audio
 *           0x02 + utf8 text → typewriter text tokens from the LM
 *
 * This hook handles the connection lifecycle + text-stream parsing. Audio
 * plumbing (mic capture, opus encode/decode, playback) is layered on top
 * in NativeCallPane via the onAudioFromServer / sendAudioFrame callbacks.
 */

export type MoshiConnectionState =
  | "idle"
  | "connecting"
  | "handshake"
  | "connected"
  | "closing"
  | "closed"
  | "error";

export interface MoshiSessionOptions {
  wsUrl: string;
  textPrompt?: string;
  voicePrompt?: string;
  onText?: (token: string) => void;
  onAudio?: (oggPage: Uint8Array) => void;
  onHandshake?: () => void;
  onError?: (err: Error) => void;
}

export interface MoshiSession {
  state: MoshiConnectionState;
  lastError: string | null;
  start: () => void;
  stop: () => void;
  sendAudioFrame: (oggPage: Uint8Array) => void;
}

/** Server-side patch overrides text_prompt anyway, but the moshi UI's URL
 * still needs SOMETHING in the query — the empty-string check in
 * server.py expects a value. We pass a single space to satisfy the
 * length>0 branch; the patched code reads MOSHI_DEFAULT_TEXT_PROMPT_FILE
 * and ignores what we send. */
const QUERY_PROMPT_PLACEHOLDER = " ";
const DEFAULT_VOICE_PROMPT = "NATF0.pt";

function buildWsUrl(base: string, opts: { textPrompt: string; voicePrompt: string }): string {
  const u = new URL(base);
  // Random seeds — moshi varies behaviour on each call. Match what the
  // stock client uses (6-digit random ints).
  const r = () => Math.floor(Math.random() * 999_999) + 1;
  u.searchParams.set("text_temperature", "0.7");
  u.searchParams.set("text_topk", "25");
  u.searchParams.set("audio_temperature", "0.8");
  u.searchParams.set("audio_topk", "250");
  u.searchParams.set("pad_mult", "0");
  u.searchParams.set("text_seed", String(r()));
  u.searchParams.set("audio_seed", String(r()));
  u.searchParams.set("repetition_penalty_context", "64");
  u.searchParams.set("repetition_penalty", "1");
  u.searchParams.set("text_prompt", opts.textPrompt);
  u.searchParams.set("voice_prompt", opts.voicePrompt);
  return u.toString();
}

export function useMoshiSession(options: MoshiSessionOptions): MoshiSession {
  const { wsUrl, textPrompt, voicePrompt, onText, onAudio, onHandshake, onError } = options;

  const wsRef = useRef<WebSocket | null>(null);
  const [state, setState] = useState<MoshiConnectionState>("idle");
  const [lastError, setLastError] = useState<string | null>(null);

  // Stable refs to user callbacks so changing them doesn't churn the WS
  const cbRef = useRef({ onText, onAudio, onHandshake, onError });
  cbRef.current = { onText, onAudio, onHandshake, onError };

  const stop = useCallback(() => {
    const ws = wsRef.current;
    if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
      setState("closing");
      ws.close(1000, "client end");
    }
    wsRef.current = null;
  }, []);

  const start = useCallback(() => {
    if (wsRef.current) return;
    setLastError(null);
    setState("connecting");

    const url = buildWsUrl(wsUrl, {
      textPrompt: textPrompt ?? QUERY_PROMPT_PLACEHOLDER,
      voicePrompt: voicePrompt ?? DEFAULT_VOICE_PROMPT,
    });

    let ws: WebSocket;
    try {
      ws = new WebSocket(url);
      ws.binaryType = "arraybuffer";
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setLastError(msg);
      setState("error");
      cbRef.current.onError?.(new Error(msg));
      return;
    }

    wsRef.current = ws;

    ws.onopen = () => {
      setState("handshake");
    };

    ws.onmessage = (ev) => {
      if (typeof ev.data === "string") {
        // moshi-personaplex sends only binary; if we get a string it's
        // an out-of-band signal we ignore for now.
        return;
      }
      const buf = new Uint8Array(ev.data as ArrayBuffer);
      if (buf.length === 0) return;

      const tag = buf[0];
      if (tag === 0x00) {
        // Handshake byte — model is ready to send/receive audio
        setState("connected");
        cbRef.current.onHandshake?.();
        return;
      }
      if (tag === 0x02) {
        // Text token (typewriter effect)
        const text = new TextDecoder("utf-8").decode(buf.slice(1));
        cbRef.current.onText?.(text);
        return;
      }
      if (tag === 0x01) {
        // Audio frame (Ogg/Opus page)
        cbRef.current.onAudio?.(buf.slice(1));
        return;
      }
      // Older moshi builds send raw Ogg pages without a 0x01 prefix;
      // detect by "OggS" magic.
      if (buf.length >= 4 && buf[0] === 0x4f && buf[1] === 0x67 && buf[2] === 0x67 && buf[3] === 0x53) {
        cbRef.current.onAudio?.(buf);
        return;
      }
      // Unknown — drop silently. Logging here would spam.
    };

    ws.onerror = () => {
      setLastError("websocket error (see browser network tab)");
      setState("error");
      cbRef.current.onError?.(new Error("websocket error"));
    };

    ws.onclose = (ev) => {
      wsRef.current = null;
      setState("closed");
      if (!ev.wasClean && ev.code !== 1000) {
        const msg = `closed unexpectedly (code ${ev.code} ${ev.reason || "no reason"})`;
        setLastError(msg);
        cbRef.current.onError?.(new Error(msg));
      }
    };
  }, [wsUrl, textPrompt, voicePrompt]);

  const sendAudioFrame = useCallback((page: Uint8Array) => {
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    // moshi.server tags binary frames by kind: 0x00 handshake, 0x01 audio,
    // 0x02 text. Without the prefix moshi logs "unknown message kind 79"
    // (79 = 'O' from the OggS magic) and discards the audio.
    const framed = new Uint8Array(page.length + 1);
    framed[0] = 0x01;
    framed.set(page, 1);
    ws.send(framed);
  }, []);

  useEffect(() => {
    return () => {
      const ws = wsRef.current;
      if (ws) {
        try {
          ws.close(1000, "unmount");
        } catch {
          // ignore
        }
        wsRef.current = null;
      }
    };
  }, []);

  return { state, lastError, start, stop, sendAudioFrame };
}
