import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { defineConfig, devices } from "@playwright/test";

/**
 * Loads the fixture credentials from `e2e.env` at the repo root, so the specs read them from
 * `process.env` instead of carrying them as literals. Deliberately hand-rolled rather than
 * pulling in `dotenv`: it is five lines for `KEY=VALUE`, and a test-only dependency that ships
 * in `node_modules` is a worse trade than five lines. Anything already in the environment wins,
 * so CI or a developer can point the suite at a different tenant without editing the file.
 */
function loadFixtureEnv(): void {
  let contents: string;
  try {
    contents = readFileSync(resolve(__dirname, "..", "e2e.env"), "utf8");
  } catch {
    return; // absent is fine — the environment may already carry them; fixtures.ts checks
  }
  for (const line of contents.split("\n")) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) continue;
    const eq = trimmed.indexOf("=");
    if (eq === -1) continue;
    const key = trimmed.slice(0, eq).trim();
    if (process.env[key] === undefined) process.env[key] = trimmed.slice(eq + 1).trim();
  }
}

loadFixtureEnv();

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
  // `next dev`'s on-demand compilation means a route's very first hit in the container's
  // lifetime can be slow on a loaded CI runner; give tests headroom beyond that first compile.
  timeout: 45_000,
  expect: { timeout: 10_000 },
  use: {
    baseURL: process.env.PLAYWRIGHT_BASE_URL ?? "http://localhost:3000",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    viewport: { width: 1440, height: 900 },
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
