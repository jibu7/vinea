import { describe, expect, it, vi, afterEach } from "vitest";

const notFound = vi.hoisted(() =>
  vi.fn(() => {
    throw new Error("NEXT_NOT_FOUND");
  }),
);
vi.mock("next/navigation", () => ({ notFound }));

import DesignLayout from "./layout";

afterEach(() => {
  vi.unstubAllEnvs();
  notFound.mockClear();
});

/**
 * The gallery is exempt from the i18n rules (see `.eslintrc.README.md`). That exemption is
 * only safe while the routes cannot be served to a tenant, so the gate is held by a test
 * rather than by the comment above it.
 */
describe("the design gallery is development-only", () => {
  it("404s in production", () => {
    vi.stubEnv("NODE_ENV", "production");
    expect(() => DesignLayout({ children: null })).toThrow("NEXT_NOT_FOUND");
    expect(notFound).toHaveBeenCalled();
  });

  it("renders in development", () => {
    vi.stubEnv("NODE_ENV", "development");
    expect(() => DesignLayout({ children: null })).not.toThrow();
    expect(notFound).not.toHaveBeenCalled();
  });
});
