"use client";

import * as SelectPrimitive from "@radix-ui/react-select";
import { Check, ChevronDown } from "lucide-react";
import { cn } from "@/lib/cn";
import { useFieldLabelId } from "./input";

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
  ariaLabel,
}: {
  options: SelectOption[];
  value?: string;
  onValueChange?: (value: string) => void;
  placeholder?: string;
  className?: string;
  /** Accessible name for contexts with no `Field` wrapper to supply one. */
  ariaLabel?: string;
}) {
  // Radix renders the trigger as a `role="combobox"` button. Unlike `Input`, `Combobox` and
  // `DatePicker`, this component never read the `Field` label id, so its only accessible name
  // was whatever text happened to be selected — and none at all before a value resolves. axe
  // scored that `button-name`, critical. Name it the same way its siblings do.
  const labelId = useFieldLabelId();
  return (
    <SelectPrimitive.Root value={value} onValueChange={onValueChange}>
      <SelectPrimitive.Trigger
        aria-label={ariaLabel}
        aria-labelledby={ariaLabel ? undefined : labelId}
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
