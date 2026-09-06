"""
_token_crypto.py — at-rest encryption for OAuth tokens (Phase 05B). Uses
Fernet (symmetric, authenticated encryption) rather than storing tokens in
plaintext: these are live bearer credentials to a connected business's Google
Analytics / Search Console account, a materially higher sensitivity than
anything else this app persists.

ANALYTICS_TOKEN_KEY must be a Fernet key (urlsafe-base64, 32 raw bytes).
Generate one with:
    python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
and put it in /etc/paisamap/db.env alongside DATABASE_URL/SECRET_KEY/
GOOGLE_CLIENT_ID. No fallback key — same "fail loudly, not silently" stance as
_auth_db.py's require_db: a token encrypted with a key that's gone is
unrecoverable by design, which is the correct failure mode for a secret, not
a bug to route around.
"""

import os


def _fernet():
    key = os.environ.get("ANALYTICS_TOKEN_KEY")
    if not key:
        raise RuntimeError("ANALYTICS_TOKEN_KEY is not configured — OAuth token storage requires it")
    from cryptography.fernet import Fernet
    return Fernet(key.encode() if isinstance(key, str) else key)


def enabled() -> bool:
    return bool(os.environ.get("ANALYTICS_TOKEN_KEY"))


def encrypt(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    return _fernet().decrypt(ciphertext.encode()).decode()
