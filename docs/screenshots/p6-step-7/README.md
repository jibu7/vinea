# P6 step 7 — Order entry transaction screens

1440x900, light and dark, captured against the dev stack by
`frontend/scripts/capture-p6-orders.ts` in a single pass on a reset database. Re-capture with:

```sh
cd frontend
OUT=../docs/screenshots/p6-step-7 npx tsx scripts/capture-p6-orders.ts
```

`ONLY` re-captures a subset, so changing one screen does not rewrite the other twenty-eight
files:

```sh
OUT=../docs/screenshots/p6-step-7 ONLY=8-landed-cost-preview npx tsx scripts/capture-p6-orders.ts
```

**One consignment, followed all the way through.** The capture script drives the real
endpoints as the signed-in owner: 40 bottles and 10 gift boxes ordered from Kivu Vintners, 25
bottles and all 10 boxes received on DN-44812, 60 000 of freight landed on what arrived, and a
restaurant order for 30 bottles and two gift packs — more bottles than the shelf holds, so the
backorder column has something to say. The same figures run from shot 2 to shot 15, which is
what lets a reviewer check the arithmetic rather than only the layout.

| # | Screen | Files |
|---|---|---|
| 1 | Purchase orders, the listing | `1-purchase-orders-{light,dark}.png` |
| 2 | One purchase order: ordered, received, remaining | `2-purchase-order-{light,dark}.png` |
| 3 | The receipt that order prepares, with what is outstanding | `3-goods-receipt-from-order-{light,dark}.png` |
| 4 | Goods received, with the unmatched total | `4-goods-received-{light,dark}.png` |
| 5 | One receipt: value, matched, unmatched | `5-goods-receipt-{light,dark}.png` |
| 6 | Landed costs, the listing | `6-landed-costs-{light,dark}.png` |
| 7 | One landed cost, share by share | `7-landed-cost-{light,dark}.png` |
| 8 | The share **preview**, before anything posts | `8-landed-cost-preview-{light,dark}.png` |
| 9 | Supplier invoice in matching mode | `9-supplier-invoice-matching-{light,dark}.png` |
| 10 | Sales orders, with the backorder column | `10-sales-orders-{light,dark}.png` |
| 11 | One sales order, a kit and its components | `11-sales-order-{light,dark}.png` |
| 12 | The order workspace, on the order grid | `12-sales-order-edit-{light,dark}.png` |
| 13 | Breakup, opened from the line | `13-breakup-dialog-{light,dark}.png` |
| 14 | Breakup, on its own screen | `14-breakup-{light,dark}.png` |
| 15 | Customer invoice prepared from the order | `15-customer-invoice-from-order-{light,dark}.png` |

Shot 3 is the half of decision 7 that is easy to miss: the receipt carries the order's **stock**
lines and not its service line, because a service is received by its invoice and never by a
goods receipt. The outstanding panel says what is still owed per line, so receiving 25 of 40 is
one number changed rather than a document keyed.

Shot 5 shows Evolution's word for the state — **Unprocessed**, over a stored status of
`received` — and the value the accrual carries. Once the invoice in shot 9 matches it, the same
screen reads **Processed** and Reverse is disabled with the reason in words: an invoice has
claimed part of this receipt, reverse that first.

Shot 8 is the landed-cost screen's whole argument. 60 000 spread by value over a receipt worth
1 225 000 gives 55 102 to the bottles and 4 898 to the boxes, shown **before** Post and computed
by the function Post runs. A preview with arithmetic of its own is a preview that will one day
show a person one set of shares and write another.

Shots 9 and 15 are P4's own invoice screens carrying item lines for the first time (decision 1):
the item, warehouse, unit and tax-code cells sit beside the account column rather than in place
of it, because an invoice for three cases of wine and a delivery charge is one document. The
panel above the grid is what the source document still has open; quantities may be lowered and
never raised, and the guard that says so lives on the endpoint that posts, not here.

Shot 11 shows a kit as the customer sees it — one line with a price — over the two component
lines the warehouse sees. Shot 13 edits that explosion for **this order only**, which is what
shot 14's "Edited by hand" chip then records, and what `kit_breakup_would_reset` protects when
the line's quantity later changes.
