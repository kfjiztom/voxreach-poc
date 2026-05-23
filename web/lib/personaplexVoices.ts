/**
 * PersonaPlex pre-shipped voice prompts (voices.tgz in
 * https://huggingface.co/nvidia/personaplex-7b-v1).
 *
 * Categories (per NVIDIA's repo docs):
 *   Natural — designed to sound conversational, day-to-day human
 *   Variety — broader range of speaking styles / character
 *
 * The filename (e.g. "NATF0.pt") is what moshi.server accepts as the
 * `voice_prompt` query string parameter on the WS handshake.
 */

export interface VoiceOption {
  /** Filename moshi expects, including the .pt extension. */
  file: string;
  /** Short label for the UI. */
  label: string;
  category: "natural-female" | "natural-male" | "variety-female" | "variety-male";
}

export const PERSONAPLEX_VOICES: VoiceOption[] = [
  // Natural female
  { file: "NATF0.pt", label: "Natural F · 0", category: "natural-female" },
  { file: "NATF1.pt", label: "Natural F · 1", category: "natural-female" },
  { file: "NATF2.pt", label: "Natural F · 2", category: "natural-female" },
  { file: "NATF3.pt", label: "Natural F · 3", category: "natural-female" },
  // Natural male
  { file: "NATM0.pt", label: "Natural M · 0", category: "natural-male" },
  { file: "NATM1.pt", label: "Natural M · 1", category: "natural-male" },
  { file: "NATM2.pt", label: "Natural M · 2", category: "natural-male" },
  { file: "NATM3.pt", label: "Natural M · 3", category: "natural-male" },
  // Variety female
  { file: "VARF0.pt", label: "Variety F · 0", category: "variety-female" },
  { file: "VARF1.pt", label: "Variety F · 1", category: "variety-female" },
  { file: "VARF2.pt", label: "Variety F · 2", category: "variety-female" },
  { file: "VARF3.pt", label: "Variety F · 3", category: "variety-female" },
  { file: "VARF4.pt", label: "Variety F · 4", category: "variety-female" },
  // Variety male
  { file: "VARM0.pt", label: "Variety M · 0", category: "variety-male" },
  { file: "VARM1.pt", label: "Variety M · 1", category: "variety-male" },
  { file: "VARM2.pt", label: "Variety M · 2", category: "variety-male" },
  { file: "VARM3.pt", label: "Variety M · 3", category: "variety-male" },
  { file: "VARM4.pt", label: "Variety M · 4", category: "variety-male" },
];

/** Default voice — matches what start.sh and our reference client send. */
export const DEFAULT_VOICE_FILE = "NATF0.pt";

/** Persist the operator's chosen voice across page reloads. */
const STORAGE_KEY = "voxreach.voice";

export function loadStoredVoice(): string {
  if (typeof window === "undefined") return DEFAULT_VOICE_FILE;
  try {
    const v = window.localStorage.getItem(STORAGE_KEY);
    if (v && PERSONAPLEX_VOICES.some((opt) => opt.file === v)) return v;
  } catch {
    // localStorage might be disabled (private mode) — fall back to default
  }
  return DEFAULT_VOICE_FILE;
}

export function storeVoice(file: string): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(STORAGE_KEY, file);
  } catch {
    // ignore
  }
}
