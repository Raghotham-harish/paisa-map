"""restore_drill_keys.py — the part of restore_drill.sh that needs Python: prove the
encryption keys in /etc/paisamap/db.env can still read the encrypted columns of a
RESTORED database.

A backup whose customer data cannot be decrypted is not a backup. That happens if
CUSTOMER_DATA_KEY / ANALYTICS_TOKEN_KEY are rotated without re-encrypting old rows,
or if the server is rebuilt and db.env is recreated with fresh keys. Row counts
cannot see it; only a real decrypt can.

Reads the db.env contents on STDIN (restore_drill.sh pipes `sudo cat` into it), so
no secret ever sits in a file, an argument, or this process's environment. Connects
to the database named in argv[1] with db.env's DATABASE_URL (same role and password,
only the database name swapped). Prints counts only — never a key, a URL, or a value.

Exit 0 = every sampled value decrypted (or there was nothing encrypted to check).
Exit 1 = at least one value failed to decrypt, a needed key is missing, or the check
could not run.
"""

import re
import sys

# (table, encrypted columns, env var holding the Fernet key)
ENCRYPTED = [
    ("customer_locations",
     ["revenue_encrypted", "rent_encrypted", "capex_encrypted", "raw_address_encrypted"],
     "CUSTOMER_DATA_KEY"),
    ("oauth_connections", ["access_token_encrypted", "refresh_token_encrypted"], "ANALYTICS_TOKEN_KEY"),
]
SAMPLE_ROWS = 500   # newest rows per table; the whole table on today's volumes


def parse_env(text):
    env = {}
    for line in text.splitlines():
        m = re.match(r"\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$", line)
        if m:
            v = m.group(2).strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
                v = v[1:-1]
            env[m.group(1)] = v
    return env


def main():
    if len(sys.argv) != 2 or not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", sys.argv[1]):
        print("usage: restore_drill_keys.py DB_NAME  (db.env on stdin)")
        return 1
    target = sys.argv[1]
    env = parse_env(sys.stdin.read())
    if not env.get("DATABASE_URL"):
        print("keys: FAIL — no DATABASE_URL in the env file")
        return 1

    from cryptography.fernet import Fernet, InvalidToken
    from sqlalchemy import create_engine, inspect, text
    from sqlalchemy.engine import make_url

    try:
        engine = create_engine(make_url(env["DATABASE_URL"]).set(database=target))
        with engine.connect() as conn:
            tables = set(inspect(conn).get_table_names())
            ok = True
            for table, cols, keyvar in ENCRYPTED:
                if table not in tables:
                    print(f"keys: {table}: table not in this dump — skipped")
                    continue
                present = {c["name"] for c in inspect(conn).get_columns(table)}
                cols = [c for c in cols if c in present]
                where = " OR ".join(f"{c} IS NOT NULL" for c in cols)
                rows = conn.execute(text(
                    f"SELECT {', '.join(cols)} FROM {table} WHERE {where} ORDER BY id DESC LIMIT {SAMPLE_ROWS}"
                )).fetchall()
                values = [v for r in rows for v in r if v is not None]
                if not values:
                    print(f"keys: {table}: no encrypted values to check")
                    continue
                if not env.get(keyvar):
                    print(f"keys: {table}: FAIL — {len(values)} encrypted values but {keyvar} is not in the env file")
                    ok = False
                    continue
                f = Fernet(env[keyvar].encode())
                bad = 0
                for v in values:
                    try:
                        f.decrypt(v.encode())
                    except (InvalidToken, ValueError):
                        bad += 1
                status = "OK" if bad == 0 else "FAIL"
                print(f"keys: {table}: {status} — {len(values) - bad}/{len(values)} values decrypt with "
                      f"{keyvar} ({len(rows)} newest rows sampled)")
                ok = ok and bad == 0
        return 0 if ok else 1
    except Exception as e:  # never echo the exception text: DB errors can carry the DSN
        print(f"keys: FAIL — could not run the check ({type(e).__name__})")
        return 1


if __name__ == "__main__":
    sys.exit(main())
