# Backups and restore

Go-live item "backups + a tested restore" (docs/PENTEST_CHECKLIST.md). Scripts live in
`paisamap-etl/db/`.

## What needs backing up

| What | Where it lives | Covered by |
|---|---|---|
| Accounts, orders, invoices, wallets, projects, uploaded customer data, API keys, OAuth tokens | Postgres database `paisamap` (the app writes no user data to disk) | `backup_db.sh`, nightly |
| Encryption keys: `CUSTOMER_DATA_KEY`, `ANALYTICS_TOKEN_KEY` (and the other secrets) | `/etc/paisamap/db.env` on the server | **Your password manager, once** (step 4 below). A dump restored without the key that encrypted it gives unreadable customer data. |
| Code, CSV signal data | GitHub | git |
| The whole server disk | Lightsail | Automatic snapshots (step 5 below) |

## Scripts

| Script | Where it runs | What it does |
|---|---|---|
| `backup_db.sh` | server, 01:30 nightly | Consistent `pg_dump` plus a manifest of every table's row count, taken in the same snapshot. Checks the archive reads back. Keeps 14 daily, 8 weekly, 12 monthly. Writes to `/home/ubuntu/backups/paisamap/`. |
| `restore_drill.sh` | server, Sundays 03:30 | Restores the newest dump into a throwaway `paisamap_drill` DB. Requires exact row counts and checks the live keys decrypt the restored customer data and OAuth tokens. Then drops the drill DB. Fails if the newest backup is older than 36 h. |
| `restore_db.sh` | server, by hand | Restores into a new database (look at old data), or replaces the live database with a rename swap, or rolls that back. |
| `pull_backups.sh` | your Mac | Copies the backups off the server and re-checks every checksum. |

Status is in two one-line files: `~/backups/paisamap/last_backup` and `~/backups/paisamap/last_drill`.

## One-time setup (after this is deployed)

Run from your Mac in zsh:

```zsh
SSH(){ ssh -i ~/.ssh/paisamap_lightsail ubuntu@paisamaps.com "$@"; }
D=/home/ubuntu/paisa-map/paisamap-etl/db

# 1. first backup (expect "[backup] OK ... 2x tables, N rows")
SSH "bash $D/backup_db.sh"

# 2. first drill (expect "[drill] PASS"; a FAIL on "keys" is important, see Troubleshooting)
SSH "bash $D/restore_drill.sh"

# 3. nightly backup + weekly drill in ubuntu's crontab (keeps the existing enrich job)
SSH "bash $D/backup_db.sh --install-cron"

# 4. copy db.env into your password manager. It goes straight to the clipboard, never on screen.
#    Paste it into a new secure note, then clear the clipboard.
SSH "sudo cat /etc/paisamap/db.env" | pbcopy
#    ...paste into the password manager, then:
pbcopy < /dev/null
#    Redo this after any secret rotation.

# 5. pull a copy to this Mac (weekly is fine; FileVault must be on)
bash paisamap-etl/db/pull_backups.sh
```

6. In the Lightsail console, open the instance, go to **Snapshots**, and turn on **Automatic snapshots**.
   Lightsail keeps the last 7 daily snapshots of the whole disk.

## Checking on it

```zsh
SSH "cat ~/backups/paisamap/last_backup ~/backups/paisamap/last_drill"
SSH "tail -20 ~/logs/backup.log"
```

`last_drill` should say `PASS` and be no more than a week old. There is no email alert yet (needs SES, U4).
Until there is, check it weekly, or glance at the `pull_backups.sh` output, which prints both lines.

## Restoring

**Look at old data without touching the site** (e.g. "what did this org's wallet look like on the 3rd?"):

```zsh
SSH "ls ~/backups/paisamap/daily"
SSH "bash $D/restore_db.sh ~/backups/paisamap/daily/paisamap-<time>.dump paisamap_look"
SSH "sudo -u postgres psql -d paisamap_look"          # look around
SSH "sudo -u postgres dropdb paisamap_look"           # when done
```

**Replace the live database** (data was deleted or corrupted). This has to run in an interactive terminal,
because it asks you to type `paisamap` to confirm:

```zsh
ssh -t -i ~/.ssh/paisamap_lightsail ubuntu@paisamaps.com "bash $D/restore_db.sh ~/backups/paisamap/LATEST --live"
```

It first restores into `paisamap_incoming` and checks it while the site stays up. Then it stops the service,
takes a `pre-restore` safety backup, swaps the database names, starts the service and calls `/api/health`.
The site is down for a few seconds. Nothing is dropped. It prints the rollback command:

```zsh
ssh -t -i ~/.ssh/paisamap_lightsail ubuntu@paisamaps.com "bash $D/restore_db.sh --rollback paisamap_prerestore_<time>"
```

**Payments after a restore.** Anything paid between the backup and the restore is missing from the restored
database: orders, credits, subscription charges. Razorpay is the record of what was actually charged.
Before reopening sales, compare Razorpay's payments list for that window against `orders`, and re-apply or refund.

**New server (the old one is gone):**
1. Create the instance from the latest Lightsail snapshot if there is one; that is the fastest path, and after it
   you only need the steps below if the snapshot is older than the newest dump.
2. Otherwise build a fresh server. Put the saved `db.env` back at `/etc/paisamap/db.env` (root, mode 600).
   Run `setup_server_db.sh` with `PAISAMAP_DB_PASSWORD` set to the password inside that `DATABASE_URL`,
   so the role matches the saved file.
3. Copy a dump plus its `.manifest` from your Mac into `/home/ubuntu/backups/paisamap/manual/`.
   Then run `restore_db.sh <dump> --live`, and then `restore_drill.sh <dump>` to confirm the keys read the data.

## Limits (what this does not do)

- **Up to 24 hours of data can be lost** (nightly dumps). For payments, Razorpay covers that gap (see above).
  If order volume grows, add continuous WAL archiving for point-in-time recovery.
- Server-side dumps are not encrypted. They sit on the same disk as the live database, readable only by
  `ubuntu`, so they add no new exposure. The Mac copy relies on FileVault.
- No alerting yet (see "Checking on it").

## How it was tested (2026-09-23, Postgres 16.15 in Docker, the app's real 22-table schema)

- Backup taken while ~26k rows were being written concurrently. The restore matched the manifest on every table.
  This proves counts and dump come from one snapshot.
- Refusals: corrupted dump (sha256), missing manifest, a manifest with one count changed, an existing target
  name, the live name without `--live`, `--live` with no confirmation and no terminal (nothing touched),
  and a truncated dump (restore rolled back, nothing half-restored).
- Live restore after deleting 1,000 rows: a held-open app connection was cut, the safety backup was taken,
  the stand-in app served restored data after the swap, and the rollback put the damaged DB back.
- Safety backup failing after the service stopped: the service came back up and the live DB was untouched.
- Drill: PASS on good data. FAIL on a wrong `CUSTOMER_DATA_KEY` (0/160 decrypt), a missing `ANALYTICS_TOKEN_KEY`,
  a wrong DB password (the password never appears in output), a 40 h old backup, no backups, a corrupted dump.
- Rotation keeps exactly 14 daily dumps with their manifests. `--install-cron` is idempotent and keeps
  existing entries. `pull_backups.sh` on macOS flags a corrupted copy.

## Troubleshooting

- **Drill says `keys: ... FAIL — 0/N values decrypt`**: the key in `db.env` is not the one that encrypted those rows.
  This happens after a key rotation that did not re-encrypt old rows. Find the old key (password manager history)
  before anything else. Until then those customer rows are unreadable in production too, not only in the backup.
- **`sudo: a password is required`**: the scripts need passwordless `sudo -u postgres` (true for `ubuntu` on Lightsail).
