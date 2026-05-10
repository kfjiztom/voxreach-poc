interface WaveformProps {
  active: boolean;
}

// Tailwind's JIT needs literal class names at build time — no template interpolation.
const ANIM_CLASSES = [
  "animate-wave-1",
  "animate-wave-2",
  "animate-wave-3",
  "animate-wave-4",
  "animate-wave-5",
];

export function Waveform({ active }: WaveformProps) {
  return (
    <div className="flex h-16 items-center justify-center gap-1.5">
      {ANIM_CLASSES.map((animClass, i) => (
        <div
          key={i}
          className={`w-1.5 rounded-full transition-colors ${
            active ? `bg-persimmon h-12 ${animClass}` : "bg-clay h-3"
          }`}
          style={{ transformOrigin: "center" }}
        />
      ))}
    </div>
  );
}
