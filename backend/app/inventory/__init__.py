"""Inventory (Master Plan §5 P5).

Step 1 is masters only: units of measure and their conversions, items and barcodes,
warehouses, the inventory transaction types and the company's inventory defaults. The stock
ledger (`stock_moves`) and the costing engine land in step 2 — nothing in this package holds
a quantity or a cost yet, and nothing ever will: they are derived from moves.
"""
