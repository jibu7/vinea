"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useMe } from "@/features/auth/hooks";
import { AppShell } from "./app-shell";

export default function ShellLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const { data: me, isPending, isError } = useMe();

  useEffect(() => {
    if (isError) router.replace("/login");
  }, [isError, router]);

  if (isPending) {
    return (
      <div className="flex min-h-screen items-center justify-center text-sm text-[var(--vinea-ink-subtle)]">
        Loading…
      </div>
    );
  }

  if (isError || !me) return null; // redirect effect above is already in flight

  return <AppShell me={me}>{children}</AppShell>;
}
