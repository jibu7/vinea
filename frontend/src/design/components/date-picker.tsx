"use client";

import { useState } from "react";
import * as PopoverPrimitive from "@radix-ui/react-popover";
import { Calendar, ChevronLeft, ChevronRight } from "lucide-react";
import { cn } from "@/lib/cn";
import { useFieldLabelId } from "./input";
import { formatDate } from "@/lib/format";

const WEEKDAYS = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"];

function daysInMonth(year: number, month: number) {
  return new Date(year, month + 1, 0).getDate();
}

/** Monday-first offset (0-6) for the 1st of the month. */
function leadingOffset(year: number, month: number) {
  const dow = new Date(year, month, 1).getDay();
  return (dow + 6) % 7;
}

function isSameDay(a: Date, b: Date) {
  return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
}

/** dd/MM/yyyy date picker — replaces native <input type="date"> everywhere in the app. */
export function DatePicker({
  value,
  onValueChange,
  placeholder = "Select date…",
  className,
}: {
  value?: Date | null;
  onValueChange?: (date: Date) => void;
  placeholder?: string;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  const [cursor, setCursor] = useState(() => value ?? new Date());
  const year = cursor.getFullYear();
  const month = cursor.getMonth();
  const today = new Date();
  const labelId = useFieldLabelId();

  const cells: Array<Date | null> = [
    ...Array(leadingOffset(year, month)).fill(null),
    ...Array.from({ length: daysInMonth(year, month) }, (_, i) => new Date(year, month, i + 1)),
  ];

  function changeMonth(delta: number) {
    setCursor(new Date(year, month + delta, 1));
  }

  return (
    <PopoverPrimitive.Root open={open} onOpenChange={setOpen}>
      <PopoverPrimitive.Trigger asChild>
        <button
          type="button"
          aria-labelledby={labelId}
          className={cn(
            "flex h-10 w-full items-center justify-between gap-2 rounded-[var(--radius-control)]",
            "border border-[var(--vinea-border-strong)] bg-[var(--vinea-surface-raised)] px-3 text-left text-sm",
            "[[data-density=dense]_&]:h-8",
            className,
          )}
        >
          <span className={value ? "" : "text-[var(--vinea-ink-subtle)]"}>{value ? formatDate(value) : placeholder}</span>
          <Calendar className="size-4 shrink-0 text-[var(--vinea-ink-subtle)]" />
        </button>
      </PopoverPrimitive.Trigger>
      <PopoverPrimitive.Portal>
        <PopoverPrimitive.Content
          align="start"
          sideOffset={4}
          className="z-50 w-72 rounded-[var(--radius-control)] border border-[var(--vinea-border)] bg-[var(--vinea-surface-raised)] p-3 shadow-[var(--elevation-2)]"
        >
          <div className="mb-2 flex items-center justify-between">
            <button type="button" onClick={() => changeMonth(-1)} aria-label="Previous month" className="rounded-[var(--radius-control)] p-1 hover:bg-[var(--vinea-surface-sunken)]">
              <ChevronLeft className="size-4" />
            </button>
            <span className="text-sm font-medium">
              {cursor.toLocaleString("en-GB", { month: "long" })} {year}
            </span>
            <button type="button" onClick={() => changeMonth(1)} aria-label="Next month" className="rounded-[var(--radius-control)] p-1 hover:bg-[var(--vinea-surface-sunken)]">
              <ChevronRight className="size-4" />
            </button>
          </div>
          <div className="grid grid-cols-7 gap-1 text-center text-xs text-[var(--vinea-ink-subtle)]">
            {WEEKDAYS.map((d) => (
              <span key={d} className="py-1">{d}</span>
            ))}
            {cells.map((date, i) => (
              <button
                key={i}
                type="button"
                disabled={!date}
                onClick={() => {
                  if (!date) return;
                  onValueChange?.(date);
                  setOpen(false);
                }}
                className={cn(
                  "rounded-[var(--radius-control)] py-1 text-sm text-[var(--vinea-ink)] hover:bg-[var(--vinea-surface-sunken)] disabled:opacity-0",
                  date && isSameDay(date, today) && "font-semibold text-[var(--vinea-brand)]",
                  date && value && isSameDay(date, value) && "bg-[var(--vinea-brand)] text-[var(--vinea-on-brand)] hover:bg-[var(--vinea-brand-strong)]",
                )}
              >
                {date?.getDate()}
              </button>
            ))}
          </div>
        </PopoverPrimitive.Content>
      </PopoverPrimitive.Portal>
    </PopoverPrimitive.Root>
  );
}

/** `yyyy-MM-dd` in, `yyyy-MM-dd` out — the shape the API speaks and the shape a draft can be
 * JSON-serialised in. Conversion goes through local Y/M/D, never `toISOString()`, which would
 * shift the day for anyone east or west of UTC. */
export function IsoDatePicker({
  value,
  onValueChange,
  placeholder,
  className,
}: {
  value: string;
  onValueChange: (value: string) => void;
  placeholder?: string;
  className?: string;
}) {
  const parsed = value ? new Date(`${value}T00:00:00`) : null;
  return (
    <DatePicker
      value={parsed && !Number.isNaN(parsed.getTime()) ? parsed : null}
      onValueChange={(date) => {
        const month = String(date.getMonth() + 1).padStart(2, "0");
        const day = String(date.getDate()).padStart(2, "0");
        onValueChange(`${date.getFullYear()}-${month}-${day}`);
      }}
      placeholder={placeholder}
      className={className}
    />
  );
}
