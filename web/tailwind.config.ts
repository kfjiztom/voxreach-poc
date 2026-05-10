import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./app/**/*.{ts,tsx}",
    "./components/**/*.{ts,tsx}",
    "./lib/**/*.{ts,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        // Hearth & Pass brand
        cream: "#faf6ef",
        clay: "#d8cdb8",
        ink: "#1f1c1a",
        persimmon: "#b8423a",
        persimmonDark: "#8b2e29",
        moss: "#6b7a4a",
        // Backstage / technical pane
        slate950: "#0a0e14",
        slate900: "#11161e",
        slate800: "#1a2230",
        slate700: "#2a3447",
        accentCyan: "#5fd3d3",
        accentAmber: "#f0b864",
        accentGreen: "#7dd181",
        accentRose: "#e07a7a",
      },
      fontFamily: {
        sans: ["ui-sans-serif", "system-ui", "-apple-system", "Segoe UI", "Helvetica Neue", "sans-serif"],
        serif: ["ui-serif", "Cambria", "Georgia", "serif"],
        mono: ["ui-monospace", "SF Mono", "Menlo", "monospace"],
      },
      animation: {
        "pulse-soft": "pulseSoft 2s ease-in-out infinite",
        "wave-1": "wave 1.2s ease-in-out infinite",
        "wave-2": "wave 1.2s ease-in-out 0.15s infinite",
        "wave-3": "wave 1.2s ease-in-out 0.30s infinite",
        "wave-4": "wave 1.2s ease-in-out 0.45s infinite",
        "wave-5": "wave 1.2s ease-in-out 0.60s infinite",
        "fade-in": "fadeIn 0.3s ease-out",
      },
      keyframes: {
        pulseSoft: {
          "0%, 100%": { opacity: "1" },
          "50%": { opacity: "0.6" },
        },
        wave: {
          "0%, 100%": { transform: "scaleY(0.4)" },
          "50%": { transform: "scaleY(1)" },
        },
        fadeIn: {
          "0%": { opacity: "0", transform: "translateY(4px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
      },
    },
  },
  plugins: [],
};

export default config;
