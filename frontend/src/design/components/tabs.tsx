"use client";

import * as TabsPrimitive from "@radix-ui/react-tabs";
import { cn } from "@/lib/cn";

export const Tabs = TabsPrimitive.Root;

export function TabsList({ className, ...props }: React.ComponentProps<typeof TabsPrimitive.List>) {
  return (
    <TabsPrimitive.List
      className={cn(
        "inline-flex items-center gap-1 rounded-[var(--radius-control)] bg-[var(--vinea-surface-sunken)] p-1",
        className,
      )}
      {...props}
    />
  );
}

export function TabsTrigger({ className, ...props }: React.ComponentProps<typeof TabsPrimitive.Trigger>) {
  return (
    <TabsPrimitive.Trigger
      className={cn(
        "rounded-[calc(var(--radius-control)-2px)] px-3 py-1.5 text-sm font-medium text-[var(--vinea-ink-muted)]",
        "data-[state=active]:bg-[var(--vinea-surface-raised)] data-[state=active]:text-[var(--vinea-ink)] data-[state=active]:shadow-[var(--elevation-1)]",
        className,
      )}
      {...props}
    />
  );
}

export const TabsContent = TabsPrimitive.Content;
