"use client";

import { useState } from "react";
import * as PopoverPrimitive from "@radix-ui/react-popover";
import { useTranslations } from "next-intl";
import { Command as CommandPrimitive } from "cmdk";
import { Check, ChevronsUpDown } from "lucide-react";
import { cn } from "@/lib/cn";
import { useFieldLabelId } from "./input";
import type { SelectOption } from "./select";

/** Typeahead combobox — used for account/transaction-type/project/branch/tax cells. */
export function Combobox({
  options,
  value,
  onValueChange,
  placeholder,
  className,
  onKeyDown,
  onFocus,
  ariaLabel,
  onSearch,
  fallbackLabel,
}: {
  options: SelectOption[];
  value?: string;
  onValueChange?: (value: string) => void;
  placeholder?: string;
  className?: string;
  onKeyDown?: React.KeyboardEventHandler<HTMLButtonElement>;
  onFocus?: React.FocusEventHandler<HTMLButtonElement>;
  /** Accessible name for contexts with no `Field` wrapper to supply one (e.g. LineGrid cells). */
  ariaLabel?: string;
  /**
   * Hand the typed text to the caller and stop filtering locally.
   *
   * Every other picker in the product holds its whole list — a chart of accounts, a branch
   * list, four tax classes — and cmdk filters it in the browser. RRA's item classification is
   * tens of thousands of rows: loading it all would be a picker nobody could use, so the
   * search runs on the server and what arrives is already the answer. Filtering that a second
   * time locally would hide rows the server chose to return.
   */
  onSearch?: (value: string) => void;
  /** What the trigger shows when `value` is set but no option carries it — a server-searched
   * list that has moved on from the selected row. Without it the control would read as empty
   * over a field that holds something, which is the defect class rule 13 exists for. */
  fallbackLabel?: string;
}) {
  const t = useTranslations("common");
  const [open, setOpen] = useState(false);
  const hint = placeholder ?? t("searchPlaceholder");
  const selected = options.find((o) => o.value === value);
  const selectedLabel = selected?.label ?? (value ? fallbackLabel : undefined);
  const labelId = useFieldLabelId();

  return (
    <PopoverPrimitive.Root open={open} onOpenChange={setOpen}>
      <PopoverPrimitive.Trigger asChild>
        <button
          type="button"
          aria-label={ariaLabel}
          aria-labelledby={ariaLabel ? undefined : labelId}
          onKeyDown={onKeyDown}
          onFocus={onFocus}
          className={cn(
            "flex h-10 w-full items-center justify-between gap-2 rounded-[var(--radius-control)]",
            "border border-[var(--vinea-border-strong)] bg-[var(--vinea-surface-raised)] px-3 text-left text-sm",
            "[[data-density=dense]_&]:h-8",
            className,
          )}
        >
          {/* One line, ellipsised: a long label ("WINE-750 · Rugari Red 750ml — 120 EA")
              in a dense grid cell must not wrap into the row above. The full text is the
              button's title, and the popover shows it whole. */}
          <span
            className={cn("truncate", selectedLabel ? "" : "text-[var(--vinea-ink-subtle)]")}
            title={selectedLabel}
          >
            {selectedLabel ?? hint}
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
          <CommandPrimitive shouldFilter={onSearch === undefined}>
            <CommandPrimitive.Input
              autoFocus
              placeholder={hint}
              onValueChange={onSearch}
              className="w-full border-b border-[var(--vinea-border)] px-3 py-2 text-sm outline-none"
            />
            <CommandPrimitive.List className="max-h-64 overflow-auto p-1">
              <CommandPrimitive.Empty className="px-3 py-2 text-sm text-[var(--vinea-ink-subtle)]">
                {t("noMatches")}
              </CommandPrimitive.Empty>
              {options.map((opt) => (
                <CommandPrimitive.Item
                  key={opt.value}
                  value={opt.label}
                  keywords={opt.keywords}
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
