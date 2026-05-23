import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "VoxReach · Hearth & Pass demo",
  description:
    "Live demo of the VoxReach AI receptionist for Hearth & Pass — built on PersonaPlex for full-duplex grounded voice.",
  // Point browsers explicitly at our SVG icon so they don't fall back to
  // requesting /favicon.ico and 404'ing.
  icons: {
    icon: [{ url: "/icon.svg", type: "image/svg+xml" }],
    shortcut: "/icon.svg",
  },
};

// Belt-and-suspenders cache-busting. The next.config.mjs headers() block is the
// primary defense; these meta tags are a secondary signal for browsers and
// proxies that might ignore the HTTP header.
export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <head>
        <meta httpEquiv="Cache-Control" content="no-store, no-cache, must-revalidate" />
        <meta httpEquiv="Pragma" content="no-cache" />
        <meta httpEquiv="Expires" content="0" />
      </head>
      <body className="min-h-screen bg-cream text-ink">{children}</body>
    </html>
  );
}
