"""Orders module (§6, §10–§11, §47–§48).

OWNS: orders, order_items, purchase_units. Checkout is atomic (§47): lock
wallet → calculate authoritative price → verify balance → order + items +
units → wallet debit → entitlement creation → audit → COMMIT. Payment source
is modeled, never hard-coded (§6). Multi-copy semantics ride on
`purchase_unit` as a first-class concept (§11).
"""

TABLES_OWNED = ("orders", "order_items", "purchase_units")
