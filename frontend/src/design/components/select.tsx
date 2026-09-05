"use client";

import * as SelectPrimitive from "@radix-ui/react-select";
import { Check, ChevronDown } from "lucide-react";
import { cn } from "@/lib/cn";

export interface SelectOption {
  value: string;
  label: string;
}

export function Select({
  options,
  value,
  onValueChange,
  placeholder = "Select…",
  className,
}: {
  options: SelectOption[];
  value?: string;
  onValueChange?: (value: string) => void;
  placeholder?: string;
  className?: string;
}) {
  return (
    <SelectPrimitive.Root value={value} onValueChange={onValueChange}>
      <SelectPrimitive.Trigger
        className={cn(
          "flex h-10 w-full items-center justify-between gap-2 rounded-[var(--radius-control)]",
          "border border-[var(--vinea-border-strong)] bg-[var(--vinea-surface-raised)] px-3 text-sm",
          "data-[placeholder]:text-[var(--vinea-ink-subtle)]",
          "[[data-density=dense]_&]:h-8",
          className,
        )}
      >
        <SelectPrimitive.Value placeholder={placeholder} />
        <SelectPrimitive.Icon>
          <ChevronDown className="size-4 text-[var(--vinea-ink-subtle)]" />
        </SelectPrimitive.Icon>
      </SelectPrimitive.Trigger>
      <SelectPrimitive.Portal>
        <SelectPrimitive.Content
          className="z-50 overflow-hidden rounded-[var(--radius-control)] border border-[var(--vinea-border)] bg-[var(--vinea-surface-raised)] shadow-[var(--elevation-2)]"
          position="popper"
          sideOffset={4}
        >
          <SelectPrimitive.Viewport className="p-1">
            {options.map((opt) => (
              <SelectPrimitive.Item
                key={opt.value}
                value={opt.value}
                className={cn(
                  "flex cursor-pointer items-center gap-2 rounded-[calc(var(--radius-control)-2px)]",
                  "px-2 py-1.5 text-sm outline-none data-[highlighted]:bg-[var(--vinea-surface-sunken)]",
                )}
              >
                <SelectPrimitive.ItemIndicator>
                  <Check className="size-3.5 text-[var(--vinea-brand)]" />
                </SelectPrimitive.ItemIndicator>
                <SelectPrimitive.ItemText>{opt.label}</SelectPrimitive.ItemText>
              </SelectPrimitive.Item>
            ))}
          </SelectPrimitive.Viewport>
        </SelectPrimitive.Content>
      </SelectPrimitive.Portal>
    </SelectPrimitive.Root>
  );
}
