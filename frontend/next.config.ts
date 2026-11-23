import path from "node:path";
import type { NextConfig } from "next";

// NEXT_PUBLIC_API_BASE_URL is inlined into the browser bundle at build time. Without it the deployed site would call
// http://127.0.0.1:8000 (the visitor's own machine), so a Vercel build fails loudly instead.
if (process.env.VERCEL && !process.env.NEXT_PUBLIC_API_BASE_URL?.trim()) {
  throw new Error("NEXT_PUBLIC_API_BASE_URL must be set in the Vercel project (e.g. https://mandimitra-api.onrender.com).");
}

const nextConfig: NextConfig = {
  cacheComponents: true,
  partialPrefetching: true,
  turbopack: {
    // Pin the workspace root to this folder so Next.js ignores lockfiles in parent directories.
    root: path.resolve(__dirname),
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;
