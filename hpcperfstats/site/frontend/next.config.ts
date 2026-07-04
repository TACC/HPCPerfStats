import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "export",
  trailingSlash: true,
  assetPrefix: "/static/frontend",
  images: { unoptimized: true },
  typescript: {
    ignoreBuildErrors: false,
  },
};

export default nextConfig;
