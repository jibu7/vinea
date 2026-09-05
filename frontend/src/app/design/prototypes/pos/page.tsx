"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { ArrowLeft, Minus, Plus, Trash2, Wine, ShoppingBasket, Grape, Package } from "lucide-react";
import { Button } from "@/design/components/button";
import { Money } from "@/design/components/money";
import { ThemeToggle } from "@/design/components/theme-toggle";
import { RWF } from "@/lib/format";

const products = [
  { id: "p1", name: "Rugari Merlot 750ml", price: 12000, icon: Wine },
  { id: "p2", name: "Rugari Rosé 750ml", price: 11000, icon: Wine },
  { id: "p3", name: "Table Grapes 1kg", price: 3500, icon: Grape },
  { id: "p4", name: "Gift Box — 2 bottle", price: 28000, icon: Package },
  { id: "p5", name: "Rugari Chardonnay 750ml", price: 13000, icon: Wine },
  { id: "p6", name: "Grape Juice 1L", price: 4500, icon: Grape },
];

type BasketLine = { id: string; name: string; price: number; qty: number };

export default function PosPrototype() {
  const [basket, setBasket] = useState<BasketLine[]>([
    { id: "p1", name: "Rugari Merlot 750ml", price: 12000, qty: 2 },
    { id: "p3", name: "Table Grapes 1kg", price: 3500, qty: 1 },
  ]);
  const [tender, setTender] = useState<"cash" | "card" | "momo">("momo");

  function addProduct(p: (typeof products)[number]) {
    setBasket((prev) => {
      const existing = prev.find((l) => l.id === p.id);
      if (existing) return prev.map((l) => (l.id === p.id ? { ...l, qty: l.qty + 1 } : l));
      return [...prev, { id: p.id, name: p.name, price: p.price, qty: 1 }];
    });
  }

  function setQty(id: string, qty: number) {
    setBasket((prev) => (qty <= 0 ? prev.filter((l) => l.id !== id) : prev.map((l) => (l.id === id ? { ...l, qty } : l))));
  }

  const subtotal = useMemo(() => basket.reduce((s, l) => s + l.price * l.qty, 0), [basket]);
  const tax = Math.round(subtotal - subtotal / 1.18);
  const total = subtotal;

  return (
    <div className="flex min-h-screen flex-col" data-density="airy">
      <header className="flex items-center justify-between border-b border-[var(--vinea-border)] bg-[var(--vinea-surface-raised)] px-6 py-3">
        <div className="flex items-center gap-3">
          <Link href="/design/prototypes/dashboard" className="text-[var(--vinea-ink-subtle)] hover:text-[var(--vinea-ink)]">
            <ArrowLeft className="size-4" />
          </Link>
          <h1 className="font-display text-lg font-semibold">Till — Register 1</h1>
        </div>
        <ThemeToggle />
      </header>

      <main className="grid flex-1 grid-cols-1 lg:grid-cols-[1fr_380px]">
        <section className="p-6">
          <h2 className="mb-4 font-display text-base font-medium text-[var(--vinea-ink-muted)]">Products</h2>
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-3">
            {products.map((p) => {
              const Icon = p.icon;
              return (
                <button
                  key={p.id}
                  onClick={() => addProduct(p)}
                  className="flex flex-col items-center gap-3 rounded-[var(--radius-card)] border border-[var(--vinea-border)] bg-[var(--vinea-surface-raised)] p-5 text-center shadow-[var(--elevation-1)] hover:border-[var(--vinea-brand)]"
                >
                  <div className="flex size-14 items-center justify-center rounded-full bg-[var(--vinea-brand-soft)] text-[var(--vinea-brand-strong)]">
                    <Icon className="size-6" />
                  </div>
                  <div>
                    <p className="text-sm font-medium">{p.name}</p>
                    <p className="mt-0.5 text-sm text-[var(--vinea-ink-muted)]"><Money amount={p.price} currency={RWF} /></p>
                  </div>
                </button>
              );
            })}
          </div>
        </section>

        <aside className="flex flex-col border-l border-[var(--vinea-border)] bg-[var(--vinea-surface-raised)]">
          <div className="flex items-center gap-2 border-b border-[var(--vinea-border)] px-5 py-3">
            <ShoppingBasket className="size-4 text-[var(--vinea-ink-subtle)]" />
            <h2 className="font-display text-base font-medium">Basket</h2>
          </div>
          <ul className="flex-1 divide-y divide-[var(--vinea-border)] overflow-auto">
            {basket.map((l) => (
              <li key={l.id} className="flex items-center justify-between px-5 py-3">
                <div>
                  <p className="text-sm font-medium">{l.name}</p>
                  <p className="text-xs text-[var(--vinea-ink-muted)]"><Money amount={l.price} currency={RWF} /> each</p>
                </div>
                <div className="flex items-center gap-2">
                  <button onClick={() => setQty(l.id, l.qty - 1)} className="rounded-[var(--radius-control)] border border-[var(--vinea-border-strong)] p-1"><Minus className="size-3.5" /></button>
                  <span className="w-5 text-center text-sm">{l.qty}</span>
                  <button onClick={() => setQty(l.id, l.qty + 1)} className="rounded-[var(--radius-control)] border border-[var(--vinea-border-strong)] p-1"><Plus className="size-3.5" /></button>
                  <button onClick={() => setQty(l.id, 0)} className="ml-1 text-[var(--vinea-danger)]"><Trash2 className="size-3.5" /></button>
                </div>
              </li>
            ))}
            {basket.length === 0 && (
              <li className="px-5 py-8 text-center text-sm text-[var(--vinea-ink-subtle)]">Basket is empty</li>
            )}
          </ul>

          <div className="space-y-1 border-t border-[var(--vinea-border)] px-5 py-4 text-sm">
            <div className="flex justify-between"><span className="text-[var(--vinea-ink-muted)]">Subtotal</span><Money amount={subtotal - tax} currency={RWF} /></div>
            <div className="flex justify-between"><span className="text-[var(--vinea-ink-muted)]">Tax (18%)</span><Money amount={tax} currency={RWF} /></div>
            <div className="flex justify-between font-display text-base font-semibold"><span>Total</span><Money amount={total} currency={RWF} /></div>
          </div>

          <div className="border-t border-[var(--vinea-border)] p-5">
            <p className="mb-2 text-xs font-medium uppercase tracking-wide text-[var(--vinea-ink-subtle)]">Tender</p>
            <div className="mb-4 grid grid-cols-3 gap-2">
              {(["cash", "card", "momo"] as const).map((t) => (
                <button
                  key={t}
                  onClick={() => setTender(t)}
                  className={`rounded-[var(--radius-control)] border px-2 py-2 text-xs font-medium capitalize ${
                    tender === t
                      ? "border-[var(--vinea-brand)] bg-[var(--vinea-brand-soft)] text-[var(--vinea-brand-strong)]"
                      : "border-[var(--vinea-border-strong)] text-[var(--vinea-ink-muted)]"
                  }`}
                >
                  {t === "momo" ? "Mobile money" : t}
                </button>
              ))}
            </div>
            <Button variant="primary" size="lg" className="w-full">Charge <Money amount={total} currency={RWF} className="text-white" /></Button>
          </div>
        </aside>
      </main>
    </div>
  );
}
