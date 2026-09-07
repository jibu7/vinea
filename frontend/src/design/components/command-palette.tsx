"use client";

import { useEffect, useState } from "react";
import * as DialogPrimitive from "@radix-ui/react-dialog";
import { Command as CommandPrimitive } from "cmdk";
import { Search } from "lucide-react";
import { cn } from "@/lib/cn";

export interface CommandPaletteItem {
  id: string;
  label: string;
  group: string;
  shortcut?: string;
  onSelect: () => void;
}

/** Ctrl+K shell: navigate, "new journal", "new cashbook entry", switch company, toggle theme. */
export function CommandPalette({ items }: { items: CommandPaletteItem[] }) {
  const [open, setOpen] = useState(false);

  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setOpen((o) => !o);
      }
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, []);

  const groups = Array.from(new Set(items.map((i) => i.group)));

  return (
    <DialogPrimitive.Root open={open} onOpenChange={setOpen}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="fixed inset-0 z-50 bg-black/40" />
        <DialogPrimitive.Content
          className="fixed left-1/2 top-24 z-50 w-full max-w-lg -translate-x-1/2 overflow-hidden rounded-[var(--radius-card)] border border-[var(--vinea-border)] bg-[var(--vinea-surface-raised)] shadow-[var(--elevation-3)]"
          aria-describedby={undefined}
        >
          <DialogPrimitive.Title className="sr-only">Command palette</DialogPrimitive.Title>
          <CommandPrimitive shouldFilter>
            <div className="flex items-center gap-2 border-b border-[var(--vinea-border)] px-4 py-3">
              <Search className="size-4 text-[var(--vinea-ink-subtle)]" />
              <CommandPrimitive.Input
                autoFocus
                placeholder="Type a command or search…"
                className="w-full bg-transparent text-sm outline-none placeholder:text-[var(--vinea-ink-subtle)]"
              />
              <kbd className="rounded border border-[var(--vinea-border)] px-1.5 py-0.5 text-[10px] text-[var(--vinea-ink-subtle)]">
                Esc
              </kbd>
            </div>
            <CommandPrimitive.List className="max-h-80 overflow-auto p-2">
              <CommandPrimitive.Empty className="px-3 py-6 text-center text-sm text-[var(--vinea-ink-subtle)]">
                No matches
              </CommandPrimitive.Empty>
              {groups.map((group) => (
                <CommandPrimitive.Group
                  key={group}
                  heading={group}
                  className="px-1 py-1 text-sm [&_[cmdk-group-heading]]:block [&_[cmdk-group-heading]]:px-2 [&_[cmdk-group-heading]]:py-1 [&_[cmdk-group-heading]]:text-xs [&_[cmdk-group-heading]]:font-medium [&_[cmdk-group-heading]]:uppercase [&_[cmdk-group-heading]]:tracking-wide [&_[cmdk-group-heading]]:text-[var(--vinea-ink-subtle)]"
                >
                  {items
                    .filter((i) => i.group === group)
                    .map((item) => (
                      <CommandPrimitive.Item
                        key={item.id}
                        value={item.label}
                        onSelect={() => {
                          item.onSelect();
                          setOpen(false);
                        }}
                        className={cn(
                          "flex cursor-pointer items-center justify-between rounded-[calc(var(--radius-control)-2px)]",
                          "px-2 py-2 text-sm data-[selected=true]:bg-[var(--vinea-surface-sunken)]",
                        )}
                      >
                        <span>{item.label}</span>
                        {item.shortcut && (
                          <kbd className="text-xs text-[var(--vinea-ink-subtle)]">{item.shortcut}</kbd>
                        )}
                      </CommandPrimitive.Item>
                    ))}
                </CommandPrimitive.Group>
              ))}
            </CommandPrimitive.List>
          </CommandPrimitive>
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}
