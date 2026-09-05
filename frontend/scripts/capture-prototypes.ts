import { chromium } from "@playwright/test";
import { mkdirSync } from "node:fs";

const outDir = "screenshots";
mkdirSync(outDir, { recursive: true });

const viewports = [
  { name: "desktop", width: 1440, height: 900 },
  { name: "laptop", width: 1280, height: 720 },
];

const pages = [
  { name: "dashboard", path: "/design/prototypes/dashboard" },
  { name: "workspace", path: "/design/prototypes/workspace" },
  { name: "pos", path: "/design/prototypes/pos" },
  { name: "design-system", path: "/design" },
];

async function main() {
  const browser = await chromium.launch();
  for (const vp of viewports) {
    const context = await browser.newContext({ viewport: { width: vp.width, height: vp.height } });
    const page = await context.newPage();
    for (const p of pages) {
      for (const theme of ["light", "dark"] as const) {
        await page.goto(`http://localhost:3000${p.path}`, { waitUntil: "networkidle" });
        if (theme === "dark") {
          await page.evaluate(() => document.documentElement.setAttribute("data-theme", "dark"));
          await page.waitForTimeout(100);
        }
        await page.screenshot({ path: `${outDir}/${p.name}-${theme}-${vp.name}.png`, fullPage: true });
      }
    }
    await context.close();
  }
  await browser.close();
  console.log("done");
}

main();
