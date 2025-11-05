import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Standard Next.js app (not static export like Tauri)
  reactStrictMode: true,

  // Enable standalone output for Docker
  output: 'standalone',
};

export default nextConfig;
