"""Inventory (Master Plan §5 P5).

Two halves. `masters` is the vocabulary — units of measure and their conversions, items and
barcodes, warehouses, the inventory transaction types and the company's inventory defaults.
`stock` is the ledger those masters describe: `stock_moves`, the weighted-average costing
engine in `costing`, and the `receive_stock()` / `issue_stock()` primitives that P6 will call
without changing them.

Nothing in this package stores a quantity or a cost on a master. What is on hand is an
arithmetic fact about the move ledger, cached in `stock_balances` / `item_cost_state` only
because reading it otherwise would cost a scan, and provable at any moment by
`stock.verify_stock_balances()`.
"""
