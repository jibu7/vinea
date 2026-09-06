"use client";

import { useEffect } from "react";
import { Button } from "@/design/components/button";

export default function GlobalError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  useEffect(() => {
    console.error(error);
  }, [error]);

  return (
    <div className="flex min-h-screen flex-col items-center justify-center gap-4 px-6 text-center">
      <h1 className="font-display text-2xl font-semibold">Something went wrong</h1>
      <p className="max-w-md text-sm text-[var(--vinea-ink-muted)]">
        The page hit an unexpected error. You can try again, or head back to the dashboard.
      </p>
      <Button variant="primary" onClick={reset}>Try again</Button>
    </div>
  );
}
