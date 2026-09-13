import { webcrypto } from "node:crypto";
import "@testing-library/jest-dom/vitest";

// Date-formatting tests assert on calendar dates; pin the runner's zone so they don't depend
// on the host machine's local offset. **Kigali, not UTC**: every date helper in the app turns
// an instant into a `YYYY-MM-DD` in the *viewer's* calendar, and UTC is the one zone where
// getting that wrong is invisible — at offset 0 a UTC rendering and a local one always agree,
// so a test written under UTC cannot tell the two apart and passes over the bug. Pinning the
// zone the product is built for makes those assertions mean something, and is closer to
// production than either UTC or the runner's incidental zone.
process.env.TZ = "Africa/Kigali";

// jsdom ships getRandomValues but not randomUUID on older/leaner builds; the
// design system (e.g. LineGrid rows) relies on crypto.randomUUID() at module load time.
if (typeof globalThis.crypto === "undefined" || typeof globalThis.crypto.randomUUID !== "function") {
  Object.defineProperty(globalThis, "crypto", { value: webcrypto, configurable: true });
}

// Radix primitives probe these during mount/measurement; jsdom doesn't implement them.
if (typeof globalThis.ResizeObserver === "undefined") {
  globalThis.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver;
}

if (typeof window !== "undefined" && !window.matchMedia) {
  window.matchMedia = (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  }) as unknown as MediaQueryList;
}

if (typeof Element !== "undefined" && !Element.prototype.hasPointerCapture) {
  Element.prototype.hasPointerCapture = () => false;
}
if (typeof Element !== "undefined" && !Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = () => {};
}
