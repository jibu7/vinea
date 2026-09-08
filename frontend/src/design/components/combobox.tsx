"use client";

import { useState } from "react";
import * as PopoverPrimitive from "@radix-ui/react-popover";
import { Command as CommandPrimitive } from "cmdk";
import { Check, ChevronsUpDown } from "lucide-react";
import { cn } from "@/lib/cn";
import type { SelectOption } from "./select";

/** Typeahead combobox — used for account/transaction-type/project/branch/tax cells. */
export function Combobox({
  options,
  value,
  onValueChange,
  placeholder = "Search…",
  className,
  onKeyDown,
  onFocus,
}: {
  options: SelectOption[];
  value?: string;
  onValueChange?: (value: string) => void;
  placeholder?: string;
  className?: string;
  onKeyDown?: React.KeyboardEventHandler<HTMLButtonElement>;
  onFocus?: React.FocusEventHandler<HTMLButtonElement>;
}) {
  const [open, setOpen] = useState(false);
  const selected = options.find((o) => o.value === value);

  return (
    <PopoverPrimitive.Root open={open} onOpenChange={setOpen}>
      <PopoverPrimitive.Trigger asChild>
        <button
          type="button"
          onKeyDown={onKeyDown}
          onFocus={onFocus}
          className={cn(
            "flex h-10 w-full items-center justify-between gap-2 rounded-[var(--radius-control)]",
            "border border-[var(--vinea-border-strong)] bg-[var(--vinea-surface-raised)] px-3 text-left text-sm",
            "[[data-density=dense]_&]:h-8",
            className,
          )}
        >
          <span className={selected ? "" : "text-[var(--vinea-ink-subtle)]"}>
            {selected?.label ?? placeholder}
          </span>
          <ChevronsUpDown className="size-4 shrink-0 text-[var(--vinea-ink-subtle)]" />
        </button>
      </PopoverPrimitive.Trigger>
      <PopoverPrimitive.Portal>
        <PopoverPrimitive.Content
          align="start"
          sideOffset={4}
          className="z-50 w-[--radix-popover-trigger-width] overflow-hidden rounded-[var(--radius-control)] border border-[var(--vinea-border)] bg-[var(--vinea-surface-raised)] shadow-[var(--elevation-2)]"
        >
          <CommandPrimitive>
            <CommandPrimitive.Input
              autoFocus
              placeholder={placeholder}
              className="w-full border-b border-[var(--vinea-border)] px-3 py-2 text-sm outline-none"
            />
            <CommandPrimitive.List className="max-h-64 overflow-auto p-1">
              <CommandPrimitive.Empty className="px-3 py-2 text-sm text-[var(--vinea-ink-subtle)]">
                No matches
              </CommandPrimitive.Empty>
              {options.map((opt) => (
                <CommandPrimitive.Item
                  key={opt.value}
                  value={opt.label}
                  onSelect={() => {
                    onValueChange?.(opt.value);
                    setOpen(false);
                  }}
                  className={cn(
                    "flex cursor-pointer items-center gap-2 rounded-[calc(var(--radius-control)-2px)]",
                    "px-2 py-1.5 text-sm data-[selected=true]:bg-[var(--vinea-surface-sunken)]",
                  )}
                >
                  <Check className={cn("size-3.5 text-[var(--vinea-brand)]", opt.value === value ? "opacity-100" : "opacity-0")} />
                  {opt.label}
                </CommandPrimitive.Item>
              ))}
            </CommandPrimitive.List>
          </CommandPrimitive>
        </PopoverPrimitive.Content>
      </PopoverPrimitive.Portal>
    </PopoverPrimitive.Root>
  );
}
