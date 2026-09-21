"""Module scaffolding per plan §42. Boundaries per §43:

- identity: sessions, devices, authentication state
- wallet: the ONLY writer of ledger_transactions / ledger_entries
- catalog / orders / entitlement / content / learning / video / admin / audit
Cross-module communication happens through service interfaces (§43).
"""
