import "@testing-library/jest-dom/vitest";

// Date-formatting tests assert on calendar dates; pin the runner's zone so they don't
// depend on the host machine's local offset.
process.env.TZ = "UTC";

// jsdom ships getRandomValues but not randomUUID on older/leaner builds; the
// design system (e.g. LineGrid rows) relies on crypto.randomUUID() at module load time.
if (typeof globalThis.crypto === "undefined" || typeof globalThis.crypto.randomUUID !== "function") {
  const { webcrypto } = await import("node:crypto");
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
