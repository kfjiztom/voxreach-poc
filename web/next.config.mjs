/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  async rewrites() {
    const sidecar = process.env.SIDECAR_URL ?? "http://localhost:8001";
    return [
      { source: "/api/sidecar/:path*", destination: `${sidecar}/api/:path*` },
    ];
  },
};

export default nextConfig;
