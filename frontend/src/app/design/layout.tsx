import { notFound } from "next/navigation";

/**
 * The design gallery and its prototypes are an internal reference for the design system,
 * not product surface. They are deliberately exempt from `react/jsx-no-literals` and from
 * the message catalogue (see `.eslintrc.README.md`) — which is only defensible while they
 * cannot be reached in a deployed app. An untranslated internal gallery served to tenants
 * is exactly how that exemption would turn into a leak, so the whole subtree 404s outside
 * development. `scripts/capture-prototypes.ts` shoots them against the dev server.
 */
export default function DesignLayout({ children }: { children: React.ReactNode }) {
  if (process.env.NODE_ENV === "production") notFound();
  return <>{children}</>;
}
