import type { NextConfig } from "next";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

const nextConfig: NextConfig = {
  output: "export",
  trailingSlash: true,
  assetPrefix: "/static/frontend",
  images: { unoptimized: true },
  typescript: {
    ignoreBuildErrors: false,
  },
  sassOptions: {
    includePaths: [path.join(__dirname, "node_modules")],
  },
  webpack(config) {
    config.module.rules.push({
      test: /node_modules[\\/]@bokeh[\\/].*(customjs|slick\.grid)\.js$/,
      use: path.join(__dirname, "scripts/bokeh-eval-loader.cjs"),
    });
    return config;
  },
};

export default nextConfig;
