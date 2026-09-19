import type { NextConfig } from "next";

// Hosting and rendering mode (static export vs. server rendering) are undecided:
// see docs/planning/open-questions.md, OQ-19.
const nextConfig: NextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
};

export default nextConfig;
