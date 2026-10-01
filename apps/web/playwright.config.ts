import { defineConfig, devices } from "@playwright/test";

const port = 3000;
const localURL = `http://127.0.0.1:${port}`;
const remoteURL = process.env.PLAYWRIGHT_BASE_URL?.replace(/\/$/, "");
const baseURL = remoteURL || localURL;

// Run `pnpm build` before local e2e: the webServer below serves the production build.
// Against a deployed origin set PLAYWRIGHT_BASE_URL (skips webServer).
// Default CI/local: `--project=smoke`. Full stack: `--project=critical` (see test:e2e:critical).
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  reporter: process.env.CI ? [["github"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL,
    trace: "on-first-retry",
  },
  projects: [
    {
      name: "smoke",
      testMatch: /smoke\.spec\.ts/,
      use: { ...devices["Desktop Chrome"] },
    },
    {
      name: "critical",
      testMatch: /critical-path\.spec\.ts/,
      timeout: 180_000,
      use: { ...devices["Desktop Chrome"] },
    },
  ],
  ...(remoteURL
    ? {}
    : {
        webServer: {
          command: `pnpm exec next start --port ${port}`,
          url: localURL,
          reuseExistingServer: !process.env.CI,
          timeout: 60_000,
        },
      }),
});
