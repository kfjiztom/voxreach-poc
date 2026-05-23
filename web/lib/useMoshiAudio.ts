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
    /** Frames discarded because the playback queue was too deep — keeps latency bounded. */
    framesDroppedForLag: number;
    /** Current playback-head lead over wall clock, in ms. Updated every 250ms. */
    playbackLeadMs: number;
  };
  /** Open mic + warm decoder. After this, call `pushOggPage` with frames from the server. */
  start: () => Promise<void>;
  /** Stop mic + drain playback queue. Safe to call multiple times. */
  stop: () => void;
  /** Hand a decoded Ogg/Opus page from the server to the playback pipeline. */
  pushOggPage: (page: Uint8Array) => void;
}

const DEFAULT_LEAD = 0.05;
const ENCODER_SAMPLE_RATE = 24000;        // moshi expects 24 kHz mic input
const PLAYBACK_SAMPLE_RATE = 48000;        // moshi OUTPUTS 48 kHz audio — verified from decoder reports
const MAX_PLAYBACK_LEAD_SEC = 0.15;        // drop frames if the queue is more than 150 ms ahead of real time
const PANIC_LATENCY_SEC = 1.5;             // hard reset playback head if we somehow accumulated this much lag
// RMS amplitude below this counts as silence — skip queuing so we don't push
// near-zero noise into the playback graph. moshi's full-duplex LM emits
// continuous audio even when "idle" (it's always thinking) — most of those
// frames are mathematical silence (~1e-5 RMS) but enough to feel like white
// noise played back-to-back. Real speech sits well above 0.005.
const SILENCE_RMS_THRESHOLD = 0.001;

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

  // Counters live in a ref to avoid re-rendering on every page (moshi sends
  // ~12-15 pages/sec — re-rendering on each kills UI responsiveness).
  // A 250ms interval copies the ref into state for the debug panel.
  const decodeStatsRef = useRef({
    pagesIn: 0,
    decodesOk: 0,
    decodesFailed: 0,
    samplesPlayed: 0,
    framesDroppedForLag: 0,
    playbackLeadMs: 0,
  });
  const [decodeStats, setDecodeStats] = useState(decodeStatsRef.current);
  const logFailuresLeftRef = useRef(3);

  // Flush counter ref to state ~4 Hz so the debug panel updates without
  // re-rendering on every audio frame. Also computes live playback lead.
  useEffect(() => {
    const id = window.setInterval(() => {
      const ctx = audioCtxRef.current;
      if (ctx) {
        const leadSec = Math.max(0, nextStartRef.current - ctx.currentTime);
        decodeStatsRef.current.playbackLeadMs = Math.round(leadSec * 1000);
      }
      setDecodeStats({ ...decodeStatsRef.current });
    }, 250);
    return () => window.clearInterval(id);
  }, []);

  // Resources to clean up on stop()
  // (mic stream is owned by opus-recorder — calling recorder.stop()
  // releases it. We don't track it directly.)
  const recorderRef = useRef<OpusRecorder | null>(null);
  const decoderRef = useRef<OggOpusDecoder | null>(null);
  const audioCtxRef = useRef<AudioContext | null>(null);

  // Playback scheduling — keeps audio gapless
  const nextStartRef = useRef<number>(0);

  // Pages that arrive before the decoder is initialized (race with start()):
  // moshi sends the OpusHead + OpusTags pages right after handshake, but our
  // async setup hasn't finished yet. We buffer them and flush in-order once
  // ready — WITHOUT the OpusHead the decoder can't produce ANY samples.
  const pendingPagesRef = useRef<Uint8Array[]>([]);
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

      // 4. AudioContext for playback at moshi's OUTPUT sample rate (48 kHz),
      //    not the mic encoder rate (24 kHz). Confirmed by reading the
      //    `sampleRate` field from the first successful decode result —
      //    moshi-personaplex outputs Ogg/Opus at 48 kHz regardless of
      //    what the input rate is. Mismatching here causes Web Audio to
      //    auto-resample, which plays everything at half speed and was
      //    the cause of "audio sounds slow" reports.
      const Ctx = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
      const ctx = new Ctx({ sampleRate: PLAYBACK_SAMPLE_RATE });
      // Autoplay rules: resume immediately on user gesture
      if (ctx.state === "suspended") {
        await ctx.resume();
      }
      audioCtxRef.current = ctx;
      nextStartRef.current = ctx.currentTime + playbackLeadSeconds;

      await recorder.start();

      // Flush any pages that arrived during the async setup. Order matters —
      // the OpusHead must reach the decoder before audio pages.
      if (pendingPagesRef.current.length > 0) {
        // eslint-disable-next-line no-console
        console.log("[moshi-audio] flushing", pendingPagesRef.current.length, "buffered pages to decoder");
        const buffered = pendingPagesRef.current;
        pendingPagesRef.current = [];
        for (const p of buffered) {
          decodeAndPlay(p, decoder as unknown as OggOpusDecoder, ctx);
        }
      }

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
    pendingPagesRef.current = [];
    logFailuresLeftRef.current = 3;
    setState("idle");
  }, []);

  // Shared decode-and-play logic used by both pushOggPage (live frames)
  // and the start()-side flush of buffered pages. Decoder + ctx are passed
  // explicitly so the start-time flush works before refs are stable for
  // the lazy callbacks above.
  function decodeAndPlay(page: Uint8Array, decoder: OggOpusDecoder, ctx: AudioContext) {
    decodeStatsRef.current.pagesIn += 1;

      // Log first 5 pages' first bytes so we can see what moshi is sending.
      // OggS magic = 0x4F 0x67 0x67 0x53. If we see something else, the
      // stream isn't Ogg-wrapped and we need a different decoder.
      if (decodeStatsRef.current.pagesIn <= 5) {
        // eslint-disable-next-line no-console
        console.log("[moshi-audio] page", decodeStatsRef.current.pagesIn, {
          len: page.length,
          firstBytes: Array.from(page.slice(0, 16))
            .map((b) => b.toString(16).padStart(2, "0"))
            .join(" "),
          ascii: Array.from(page.slice(0, 4))
            .map((b) => (b >= 32 && b < 127 ? String.fromCharCode(b) : "."))
            .join(""),
        });
      }

      decoder
        .decode(page)
        .then(({ channelData, samplesDecoded, sampleRate }) => {
          if (decodeStatsRef.current.pagesIn <= 5) {
            // eslint-disable-next-line no-console
            console.log("[moshi-audio] decode result", decodeStatsRef.current.pagesIn, {
              samplesDecoded,
              sampleRate,
              channelCount: channelData.length,
              ch0Length: channelData[0]?.length ?? 0,
            });
          }
          if (samplesDecoded === 0 || channelData.length === 0 || !channelData[0]?.length) {
            // OpusHead / OpusTags header pages have no audio — counts as ok decode
            decodeStatsRef.current.decodesOk += 1;
            return;
          }
          const ch0 = channelData[0];

          // Silence gate — moshi emits continuous audio even when "idle",
          // and those near-zero frames sound like white noise when played
          // back-to-back through the speakers. Skip them entirely. We
          // ALSO advance nextStartRef so silence intervals don't show
          // up as latency: when Vox starts speaking again the first real
          // frame plays immediately at "now + 5 ms" instead of after a
          // queue of silent buffers.
          let sumSquares = 0;
          for (let i = 0; i < ch0.length; i++) sumSquares += ch0[i] * ch0[i];
          const rms = Math.sqrt(sumSquares / ch0.length);
          if (rms < SILENCE_RMS_THRESHOLD) {
            decodeStatsRef.current.decodesOk += 1;
            // Don't accumulate silence in the playback queue
            const ctxNow = ctx.currentTime;
            if (nextStartRef.current < ctxNow) nextStartRef.current = ctxNow;
            return;
          }

          // PANIC reset: if latency somehow ballooned past 1.5s (the
          // drop-frames logic should normally prevent this, but bursts
          // of unfiltered speech can briefly push past), throw away the
          // backlog and resume at "now". Audible glitch but recovers
          // instantly rather than the conversation drifting further
          // and further behind real time.
          const now = ctx.currentTime;
          if (nextStartRef.current > now + PANIC_LATENCY_SEC) {
            nextStartRef.current = now + 0.05;
            decodeStatsRef.current.framesDroppedForLag += 1;
            decodeStatsRef.current.decodesOk += 1;
            return;
          }

          // Cap latency: if the playback head is already too far ahead of
          // wall clock, the queue is full (moshi probably burst-sent a few
          // frames after a stall). Drop this frame so already-queued
          // buffers play out and nextStartRef catches back up to now.
          if (nextStartRef.current > now + MAX_PLAYBACK_LEAD_SEC) {
            decodeStatsRef.current.framesDroppedForLag += 1;
            decodeStatsRef.current.decodesOk += 1;
            return;
          }

          const buf = ctx.createBuffer(1, ch0.length, sampleRate);
          buf.getChannelData(0).set(ch0);

          const src = ctx.createBufferSource();
          src.buffer = buf;
          src.connect(ctx.destination);

          // Schedule at the next free slot. If we've fallen BEHIND real
          // time (network stall), jump forward to "now + 5ms" so we
          // don't try to schedule in the past.
          const startAt = Math.max(nextStartRef.current, now + 0.005);
          src.start(startAt);
          nextStartRef.current = startAt + buf.duration;

          decodeStatsRef.current.decodesOk += 1;
          decodeStatsRef.current.samplesPlayed += samplesDecoded;
        })
        .catch((err) => {
          decodeStatsRef.current.decodesFailed += 1;
          if (logFailuresLeftRef.current > 0) {
            logFailuresLeftRef.current -= 1;
            // eslint-disable-next-line no-console
            console.warn("[moshi-audio] decode failed", {
              pageLen: page.length,
              firstBytes: Array.from(page.slice(0, 8)).map((b) => b.toString(16).padStart(2, "0")).join(" "),
              error: err instanceof Error ? err.message : String(err),
            });
          }
        });
  }

  const pushOggPage = useCallback((page: Uint8Array) => {
    const decoder = decoderRef.current;
    const ctx = audioCtxRef.current;
    if (!decoder || !ctx) {
      // Race: page arrived before decoder finished initializing. Buffer it —
      // the start() async flow will flush this list once it's ready.
      // The OpusHead must be preserved or NO subsequent audio will decode.
      pendingPagesRef.current.push(page);
      return;
    }
    decodeAndPlay(page, decoder, ctx);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

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
