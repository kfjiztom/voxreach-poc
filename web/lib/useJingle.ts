"use client";

import { useCallback, useEffect, useRef } from "react";

/**
 * Tiny synthesized jingle for the "Start call" → "Vox speaks" gap.
 * No audio files — built with OscillatorNode + GainNode so it's
 * instant on first click (no network fetch) and works offline.
 *
 * The melody is a major-chord ascending arpeggio (C → E → G → C),
 * about 600 ms total. Warm bell-like envelope (fast attack,
 * exponential decay) so it sounds like a doorbell / shop chime
 * rather than a beep.
 */

type Note = { freqHz: number; startMs: number; durationMs: number };

// C-major arpeggio in the 5th octave — universally pleasant
const MELODY: Note[] = [
  { freqHz: 523.25, startMs: 0,   durationMs: 220 }, // C5
  { freqHz: 659.25, startMs: 130, durationMs: 220 }, // E5
  { freqHz: 783.99, startMs: 260, durationMs: 320 }, // G5
];

export interface Jingle {
  play: () => void;
}

export function useJingle(): Jingle {
  const ctxRef = useRef<AudioContext | null>(null);

  // Lazily create the AudioContext on first .play() — browsers require a
  // user gesture (the Start-call click is one) before audio can start.
  // Reuse across plays for snappy second-call response.
  const getCtx = useCallback((): AudioContext => {
    if (ctxRef.current && ctxRef.current.state !== "closed") return ctxRef.current;
    const Ctx = window.AudioContext ||
      (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
    const ctx = new Ctx();
    ctxRef.current = ctx;
    return ctx;
  }, []);

  const play = useCallback(() => {
    try {
      const ctx = getCtx();
      if (ctx.state === "suspended") {
        // Best-effort resume — if it rejects we silently skip the jingle
        // (better than crashing the call start)
        ctx.resume().catch(() => {/* */});
      }
      const now = ctx.currentTime;
      for (const note of MELODY) {
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();
        osc.type = "sine";              // bell-like
        osc.frequency.value = note.freqHz;
        const startAt = now + note.startMs / 1000;
        const endAt = startAt + note.durationMs / 1000;
        // Envelope: 10ms attack, exponential decay
        gain.gain.setValueAtTime(0, startAt);
        gain.gain.linearRampToValueAtTime(0.18, startAt + 0.01);
        gain.gain.exponentialRampToValueAtTime(0.001, endAt);
        osc.connect(gain);
        gain.connect(ctx.destination);
        osc.start(startAt);
        osc.stop(endAt + 0.05);
      }
    } catch {
      // Audio not allowed (autoplay policy, no output device, etc.) — fail
      // silently. The call itself is what matters; jingle is cosmetic.
    }
  }, [getCtx]);

  // Don't close the context — let it live for the page lifetime so we can
  // play the jingle on every new call without re-creating it. The browser
  // will tear it down on tab close anyway.
  useEffect(() => () => { /* keep ctx alive */ }, []);

  return { play };
}
