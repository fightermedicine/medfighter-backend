"""Wallet module (§2 P2, §7–§9, §43, §66).

OWNS: wallet_accounts, ledger_transactions, ledger_entries — and is the ONLY
writer of them. Deposits arrive here for admin review (§7); approval creates a
balanced double-entry transaction (§8). No `balance +=` paths, ever.
"""

TABLES_OWNED = ("wallet_accounts", "ledger_transactions", "ledger_entries", "deposits")
