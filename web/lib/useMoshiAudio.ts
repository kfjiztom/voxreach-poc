"use client";

import { useCallback, useEffect, useRef, useState } from "react";

/**
 * Audio pipeline for direct moshi.server WebSocket sessions.
 *
 * Capture path (mic → moshi):
 *   getUserMedia(24kHz mono) → opus-recorder → Ogg/Opus pages → onPage()
 *   The caller hands onPage() output to the WS as binary frames.
 *
 * Playback path (moshi → speakers):
 *   pushOggPage(bytes) → @wasm-audio-decoders/ogg-opus → Float32 PCM →
 *     AudioBufferSourceNode chain → speakers
 *   We queue each decoded buffer at the next playable timestamp so the
 *   browser doesn't gap between pages.
 *
 * State machine:
 *   "idle" → "starting" → "running" → "stopping" → "idle"
 *   "error" on failure (mic permission denied, codec init failed, etc.)
 *
 * Notes on design choices:
 *   - opus-recorder's worker is copied to /audio/encoderWorker.min.js by
 *     a postinstall script (see scripts/copy-audio-workers.mjs).
 *   - moshi expects 24 kHz mono input. The recorder is configured to
 *     resample whatever the mic gives us down to 24 kHz.
 *   - We schedule playback with a small lead time (50 ms) to absorb
 *     network jitter without piling up latency. Larger lead helps with
 *     Thunder's GPU jitter at cost of perceived delay.
 */

export type AudioState = "idle" | "starting" | "running" | "stopping" | "error";

export interface MoshiAudioOptions {
  /** Called with each encoded Ogg/Opus page captured from the mic. */
  onCapturePage: (page: Uint8Array) => void;
  /** Initial scheduling lead — higher = more jitter tolerance, more lag. Default 0.05s. */
  playbackLeadSeconds?: number;
  /** Sample rate moshi expects. PersonaPlex/Moshi defaults to 24 kHz. */
  encoderSampleRate?: number;
}

export interface MoshiAudio {
  state: AudioState;
  lastError: string | null;
  /** Decode-path counters for diagnostics — surfaced in the debug panel. */
  decodeStats: {
    pagesIn: number;
    decodesOk: number;
    decodesFailed: number;
    samplesPlayed: number;
  };
  /** Open mic + warm decoder. After this, call `pushOggPage` with frames from the server. */
  start: () => Promise<void>;
  /** Stop mic + drain playback queue. Safe to call multiple times. */
  stop: () => void;
  /** Hand a decoded Ogg/Opus page from the server to the playback pipeline. */
  pushOggPage: (page: Uint8Array) => void;
}

const DEFAULT_LEAD = 0.05;
const ENCODER_SAMPLE_RATE = 24000;

/* opus-recorder is a CommonJS module — we lazy import it on .start() to
   avoid pulling Web Worker setup into the SSR bundle. */
type OpusRecorder = {
  start: () => Promise<void>;
  stop: () => Promise<void>;
  ondataavailable: (chunk: Uint8Array) => void;
  onstop?: () => void;
  onerror?: (err: Error) => void;
};

/* The decoder package exposes a webworker-friendly class. We only care
   about decode() and reset(). */
type OggOpusDecoder = {
  ready: Promise<void>;
  decode: (oggBytes: Uint8Array) => Promise<{
    channelData: Float32Array[];
    samplesDecoded: number;
    sampleRate: number;
  }>;
  free: () => void;
};

export function useMoshiAudio(options: MoshiAudioOptions): MoshiAudio {
  const { onCapturePage, playbackLeadSeconds = DEFAULT_LEAD, encoderSampleRate = ENCODER_SAMPLE_RATE } = options;

  const [state, setState] = useState<AudioState>("idle");
  const [lastError, setLastError] = useState<string | null>(null);
  const [decodeStats, setDecodeStats] = useState({
    pagesIn: 0,
    decodesOk: 0,
    decodesFailed: 0,
    samplesPlayed: 0,
  });

  // Resources to clean up on stop()
  // (mic stream is owned by opus-recorder — calling recorder.stop()
  // releases it. We don't track it directly.)
  const recorderRef = useRef<OpusRecorder | null>(null);
  const decoderRef = useRef<OggOpusDecoder | null>(null);
  const audioCtxRef = useRef<AudioContext | null>(null);

  // Playback scheduling — keeps audio gapless
  const nextStartRef = useRef<number>(0);
  // Stable ref to the callback so identity changes don't churn recorder
  const onCaptureRef = useRef(onCapturePage);
  onCaptureRef.current = onCapturePage;

  const setErr = useCallback((msg: string) => {
    setLastError(msg);
    setState("error");
    // eslint-disable-next-line no-console
    console.error("[moshi-audio]", msg);
  }, []);

  const start = useCallback(async () => {
    if (state === "running" || state === "starting") return;
    setState("starting");
    setLastError(null);

    try {
      // 1. Decoder for incoming audio (warm BEFORE mic, so first frames
      //    don't queue up undecoded).
      const decoderMod = await import("ogg-opus-decoder");
      const decoder = new decoderMod.OggOpusDecoderWebWorker();
      await decoder.ready;
      decoderRef.current = decoder as unknown as OggOpusDecoder;

      // 2. opus-recorder handles mic capture itself via mediaTrackConstraints.
      //    Passing a pre-acquired MediaStream isn't part of the v8 API —
      //    attempting that causes `MessageChannel cannot clone MediaStream`
      //    because the recorder tries to postMessage it to its worker.
      const recorderMod = await import("opus-recorder");
      const RecorderCtor = (recorderMod.default ??
        (recorderMod as unknown as { Recorder: unknown }).Recorder) as unknown as new (
        opts: Record<string, unknown>,
      ) => OpusRecorder;

      const recorder = new RecorderCtor({
        encoderPath: "/audio/encoderWorker.min.js",
        streamPages: true,             // emit pages as they're encoded
        encoderApplication: 2049,      // OPUS_APPLICATION_AUDIO (music+voice)
        encoderFrameSize: 80,          // 80 ms per page — matches moshi's frame budget
        encoderSampleRate,             // 24 kHz
        numberOfChannels: 1,
        maxFramesPerPage: 1,           // smallest pages = lowest latency
        resampleQuality: 3,
        mediaTrackConstraints: {
          channelCount: 1,
          sampleRate: encoderSampleRate,
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });

      recorder.ondataavailable = (page: Uint8Array) => {
        onCaptureRef.current(page);
      };
      recorder.onerror = (e) => setErr(`recorder error: ${e.message}`);
      recorder.onstop = () => {
        // recorder shut down — no-op here, caller drives lifecycle
      };

      recorderRef.current = recorder;

      // 4. AudioContext for playback
      const Ctx = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
      const ctx = new Ctx({ sampleRate: encoderSampleRate });
      // Autoplay rules: resume immediately on user gesture
      if (ctx.state === "suspended") {
        await ctx.resume();
      }
      audioCtxRef.current = ctx;
      nextStartRef.current = ctx.currentTime + playbackLeadSeconds;

      await recorder.start();
      setState("running");
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      // Clean up any partials
      try { recorderRef.current?.stop(); } catch {/* */}
      try { decoderRef.current?.free(); } catch {/* */}
      try { audioCtxRef.current?.close(); } catch {/* */}
      recorderRef.current = null;
      decoderRef.current = null;
      audioCtxRef.current = null;
      setErr(msg);
    }
  }, [encoderSampleRate, playbackLeadSeconds, setErr, state]);

  const stop = useCallback(() => {
    setState("stopping");
    try { recorderRef.current?.stop(); } catch {/* */}
    try { decoderRef.current?.free(); } catch {/* */}
    try { audioCtxRef.current?.close(); } catch {/* */}
    recorderRef.current = null;
    decoderRef.current = null;
    audioCtxRef.current = null;
    nextStartRef.current = 0;
    setState("idle");
  }, []);

  const pushOggPage = useCallback(
    (page: Uint8Array) => {
      const decoder = decoderRef.current;
      const ctx = audioCtxRef.current;
      if (!decoder || !ctx) {
        // eslint-disable-next-line no-console
        console.warn("[moshi-audio] page received before decoder ready, dropping", page.length);
        return;
      }

      setDecodeStats((s) => ({ ...s, pagesIn: s.pagesIn + 1 }));

      decoder
        .decode(page)
        .then(({ channelData, samplesDecoded, sampleRate }) => {
          if (samplesDecoded === 0 || channelData.length === 0 || !channelData[0]?.length) {
            // OpusHead / OpusTags header pages have no audio — counts as ok decode
            setDecodeStats((s) => ({ ...s, decodesOk: s.decodesOk + 1 }));
            return;
          }
          const ch0 = channelData[0];

          const buf = ctx.createBuffer(1, ch0.length, sampleRate);
          buf.getChannelData(0).set(ch0);

          const src = ctx.createBufferSource();
          src.buffer = buf;
          src.connect(ctx.destination);

          // Schedule at the next free slot. If we've fallen behind real
          // time (network stall or first frame after a long gap), jump
          // forward to "now + lead" so we don't accumulate latency.
          const now = ctx.currentTime;
          const startAt = Math.max(nextStartRef.current, now + 0.005);
          src.start(startAt);
          nextStartRef.current = startAt + buf.duration;

          setDecodeStats((s) => ({
            ...s,
            decodesOk: s.decodesOk + 1,
            samplesPlayed: s.samplesPlayed + samplesDecoded,
          }));
        })
        .catch((err) => {
          setDecodeStats((s) => ({ ...s, decodesFailed: s.decodesFailed + 1 }));
          // Log first 3 failures to avoid console spam
          if (decodeStats.decodesFailed < 3) {
            // eslint-disable-next-line no-console
            console.warn("[moshi-audio] decode failed", {
              pageLen: page.length,
              firstBytes: Array.from(page.slice(0, 8)).map((b) => b.toString(16).padStart(2, "0")).join(" "),
              error: err instanceof Error ? err.message : String(err),
            });
          }
        });
    },
    // decodeStats.decodesFailed is only used for log gating — safe to omit from deps
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  );

  // Unmount cleanup
  useEffect(() => {
    return () => {
      try { recorderRef.current?.stop(); } catch {/* */}
      try { decoderRef.current?.free(); } catch {/* */}
      try { audioCtxRef.current?.close(); } catch {/* */}
    };
  }, []);

  return { state, lastError, decodeStats, start, stop, pushOggPage };
}
