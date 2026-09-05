"use client";

import { createContext, useCallback, useContext, useMemo, useState } from "react";
import * as ToastPrimitive from "@radix-ui/react-toast";
import { cn } from "@/lib/cn";

type ToastTone = "neutral" | "success" | "danger";
type ToastItem = { id: number; title: string; description?: string; tone: ToastTone };

const ToastContext = createContext<{ show: (t: Omit<ToastItem, "id">) => void } | null>(null);

/** Maps API error codes (unbalanced_entry, period_closed, …) to i18n'd toasts elsewhere. */
export function useToast() {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error("useToast must be used within <ToastProvider>");
  return ctx;
}

const toneClass: Record<ToastTone, string> = {
  neutral: "border-[var(--vinea-border)]",
  success: "border-[var(--vinea-success)]",
  danger: "border-[var(--vinea-danger)]",
};

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);

  const show = useCallback((t: Omit<ToastItem, "id">) => {
    setItems((prev) => [...prev, { ...t, id: Date.now() }]);
  }, []);

  const value = useMemo(() => ({ show }), [show]);

  return (
    <ToastContext.Provider value={value}>
      <ToastPrimitive.Provider swipeDirection="right">
        {children}
        {items.map((item) => (
          <ToastPrimitive.Root
            key={item.id}
            duration={4000}
            onOpenChange={(open) => {
              if (!open) setItems((prev) => prev.filter((i) => i.id !== item.id));
            }}
            className={cn(
              "rounded-[var(--radius-control)] border-l-4 bg-[var(--vinea-surface-raised)] p-3 shadow-[var(--elevation-2)]",
              toneClass[item.tone],
            )}
          >
            <ToastPrimitive.Title className="text-sm font-medium">{item.title}</ToastPrimitive.Title>
            {item.description && (
              <ToastPrimitive.Description className="mt-0.5 text-xs text-[var(--vinea-ink-muted)]">
                {item.description}
              </ToastPrimitive.Description>
            )}
          </ToastPrimitive.Root>
        ))}
        <ToastPrimitive.Viewport className="fixed bottom-4 right-4 z-50 flex w-80 flex-col gap-2" />
      </ToastPrimitive.Provider>
    </ToastContext.Provider>
  );
}
