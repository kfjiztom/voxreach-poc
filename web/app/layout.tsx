import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "VoxReach · Hearth & Pass demo",
  description:
    "Live demo of the VoxReach AI receptionist for Hearth & Pass — built on MoshiRAG for full-duplex grounded voice.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen bg-cream text-ink">{children}</body>
    </html>
  );
}
