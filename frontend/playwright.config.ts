import { defineConfig, devices } from "@playwright/test";

/**
 * Runs against a live stack (frontend + backend + Postgres) — bring it up with
 * `docker compose up -d db backend frontend` and seed fixtures with
 * `docker compose exec backend uv run python -m app.scripts.seed_e2e` before `npm run e2e`.
 * CI wires up the same sequence (see `.github/workflows/ci.yml`, `frontend` job, `e2e` step).
 */
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  workers: 1,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  timeout: 30_000,
  expect: { timeout: 10_000 },
  use: {
    baseURL: process.env.PLAYWRIGHT_BASE_URL ?? "http://localhost:3000",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    viewport: { width: 1440, height: 900 },
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
