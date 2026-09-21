"""Catalog module (§10, §17, §64).

OWNS: products, product_versions, categories, price_rules, bundles,
bundle_items. Pricing is data-driven and server-calculated; orders snapshot
the price so later rule changes can't rewrite history (§10).
"""

TABLES_OWNED = (
    "products",
    "product_versions",
    "categories",
    "price_rules",
    "bundles",
    "bundle_items",
)
