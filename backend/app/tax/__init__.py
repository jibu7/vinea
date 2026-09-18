"""Tax returns (P7 decision 12).

The VAT return is a **query over `journal_lines` and nothing else**, which is what makes it
tie to the VAT accounts by construction rather than by reconciliation. Nothing in this package
computes tax: the tax a line attracted was computed once, by the P4 engine, at the moment it
posted, and a return that recomputed it could disagree with the ledger it is meant to report.

Country-neutrality (rule 12): the sections are keyed on `tax_codes.nature` and on the account
a line posted to, never on a country's code table. `VAT-IN-IMP` reaches this package as a
seeded tax code like any other, and nothing here imports `app.fiscal.rwanda`.
"""
