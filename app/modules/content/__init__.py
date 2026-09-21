"""Content module (§18–§22, §62–§64).

OWNS: content_assets, content_packages. Source files never become client
artifacts (P3); packaging/encryption happens in workers (§50) and delivery is
license-gated ciphertext only (§20).
"""

TABLES_OWNED = ("content_assets", "content_packages")
