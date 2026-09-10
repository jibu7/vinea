import type { NextConfig } from "next";
import createNextIntlPlugin from "next-intl/plugin";

const withNextIntl = createNextIntlPlugin("./src/i18n/request.ts");

const nextConfig: NextConfig = {
  output: "standalone",
  // Playwright writes traces, screenshots and its HTML report *inside* this directory on
  // every run, and the whole directory is bind-mounted into the dev container. `next dev`
  // watched those writes and recompiled through each e2e run — enough of a storm, after a few
  // runs, that login stopped hydrating inside Playwright's timeout and the suite failed for
  // reasons that had nothing to do with the code. Neither directory is imported by anything.
  webpack: (config) => ({
    ...config,
    watchOptions: {
      ...config.watchOptions,
      ignored: ["**/node_modules/**", "**/.git/**", "**/test-results/**", "**/playwright-report/**"],
    },
  }),
};
export default withNextIntl(nextConfig);
