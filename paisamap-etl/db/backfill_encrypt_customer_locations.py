#!/usr/bin/env python3
"""
backfill_encrypt_customer_locations.py — Phase H1: one-time (and safely
re-runnable) backfill that encrypts revenue/rent/capex/raw_address on every
customer_locations row written before this feature existed.

  DATABASE_URL=postgresql+psycopg2://... CUSTOMER_DATA_KEY=... \
    python3 db/backfill_encrypt_customer_locations.py

Idempotent — only touches rows where a plaintext value is present and its
_encrypted twin is still NULL, so re-running after new uploads land is safe.
Does NOT remove the legacy plaintext columns — that stays a separate, later,
even-more-cautious step once there's real confidence in the encrypted data
and the key is durably backed up.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "etl"))
import _auth_db  # noqa: E402
import _customer_data_crypto as _cdc  # noqa: E402


def main():
    if not _auth_db.enabled():
        print("DATABASE_URL is not set — nothing to do.")
        sys.exit(1)
    if not _cdc.enabled():
        print("CUSTOMER_DATA_KEY is not set — cannot encrypt anything without it.")
        print("Generate one with:")
        print('  python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"')
        sys.exit(1)

    print("Applying schema (if not already present)…")
    _auth_db.init_schema()
    _auth_db.migrate_schema()

    print("Backfilling customer_locations encryption…")
    counts = _auth_db.backfill_encrypt_customer_locations()
    print(f"  Rows seen:          {counts['rows_seen']}")
    print(f"  revenue encrypted:  {counts['revenue']}")
    print(f"  rent encrypted:     {counts['rent']}")
    print(f"  capex encrypted:    {counts['capex']}")
    print(f"  address encrypted:  {counts['raw_address']}")
    print("Done. Legacy plaintext columns were left in place — see this")
    print("script's docstring for why, and when that changes.")


if __name__ == "__main__":
    main()
