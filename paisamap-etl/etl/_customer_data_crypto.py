"""
_customer_data_crypto.py — at-rest encryption for uploaded customer business
data (Phase H1): a store's revenue / rent / capex / street address, entered
by hand or uploaded from a spreadsheet via customer_data.py. Real financial
and location data about a customer's own business — sensitive enough to
encrypt, same reasoning as _token_crypto.py's OAuth tokens, but a genuinely
different data class (financial/PII vs. a bearer credential) with its own
key, so rotating or, worst case, compromising one key never affects the
other's blast radius.

CUSTOMER_DATA_KEY must be a Fernet key (urlsafe-base64, 32 raw bytes).
Generate one with:
    python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
and put it in /etc/paisamap/db.env alongside ANALYTICS_TOKEN_KEY et al.

Same "fail loudly, not silently" stance as _token_crypto.py: no fallback key,
and callers do NOT check enabled() before writing — encrypting new customer
data is mandatory once this module is wired in, not best-effort, so a
missing key must break the upload commit loudly rather than silently
persist plaintext. enabled() exists only for read paths, which must still
work against rows written before this key existed (see the customer_locations
migration note in _auth_db.py) without demanding the key be present just to
list already-plaintext legacy rows.
"""

import os


def _fernet():
    key = os.environ.get("CUSTOMER_DATA_KEY")
    if not key:
        raise RuntimeError("CUSTOMER_DATA_KEY is not configured — customer data encryption requires it")
    from cryptography.fernet import Fernet
    return Fernet(key.encode() if isinstance(key, str) else key)


def enabled() -> bool:
    return bool(os.environ.get("CUSTOMER_DATA_KEY"))


def encrypt(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    return _fernet().decrypt(ciphertext.encode()).decode()
