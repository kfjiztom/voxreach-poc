/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  async rewrites() {
    const sidecar = process.env.SIDECAR_URL ?? "http://localhost:8001";
    return [
      { source: "/api/sidecar/:path*", destination: `${sidecar}/api/:path*` },
    ];
  },
  // Disable HTML/page caching at every layer (browser, Cloudflare edge, RunPod
  // proxy). The hashed-name JS/CSS chunks under /_next/static/ are still
  // long-cached because their filenames change on every build — only the HTML
  // and dynamic routes get no-store. This prevents the "I rebuilt but the
  // browser still shows yesterday's UI" trap that's bitten us repeatedly.
  async headers() {
    return [
      {
        source: "/",
        headers: [
          { key: "Cache-Control", value: "no-store, no-cache, must-revalidate, max-age=0" },
          { key: "Pragma", value: "no-cache" },
          { key: "Expires", value: "0" },
        ],
      },
      {
        source: "/api/:path*",
        headers: [
          { key: "Cache-Control", value: "no-store, no-cache, must-revalidate, max-age=0" },
        ],
      },
    ];
  },
};

export default nextConfig;
