import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Vinea ERP",
  description: "Cloud ERP for East African SMEs",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen bg-stone-50 text-[var(--vinea-ink)] antialiased">{children}</body>
    </html>
  );
}
