#!/usr/bin/env bash
# pull_backups.sh — copy the server's database backups to THIS machine (run on your Mac), so a
# lost or broken server does not take its backups with it. Re-checks every dump's sha256
# against its manifest after the copy; a mismatch fails loudly.
#
#   bash paisamap-etl/db/pull_backups.sh              # -> ~/PaisaMapBackups
#   DEST=/Volumes/Backup/paisamap bash paisamap-etl/db/pull_backups.sh
#
# Copies daily/weekly/monthly/manual as they are on the server (files the server has pruned are
# pruned here too, except monthly/ and manual/, which are kept forever locally). The dumps hold
# customer data: keep DEST on an encrypted disk (FileVault on a Mac).
set -euo pipefail
umask 077

KEY="${SSH_KEY:-$HOME/.ssh/paisamap_lightsail}"
HOST="${HOST:-ubuntu@paisamaps.com}"
SRC="${SRC:-/home/ubuntu/backups/paisamap}"
DEST="${DEST:-$HOME/PaisaMapBackups}"

mkdir -p "$DEST"
for sub in daily weekly monthly manual; do
  del=""; case "$sub" in daily|weekly) del="--delete" ;; esac
  rsync -a $del -e "ssh -i $KEY" "$HOST:$SRC/$sub/" "$DEST/$sub/"
done
rsync -a -e "ssh -i $KEY" "$HOST:$SRC/last_backup" "$HOST:$SRC/last_drill" "$DEST/" 2>/dev/null || true

bad=0; n=0
while IFS= read -r dump; do
  man="${dump%.dump}.manifest"
  want="$(sed -n 's/^dump_sha256=//p' "$man" 2>/dev/null || true)"
  got="$(shasum -a 256 "$dump" | cut -d' ' -f1)"
  n=$((n + 1))
  [ -n "$want" ] && [ "$want" = "$got" ] || { echo "BAD: $dump (checksum does not match its manifest)"; bad=$((bad + 1)); }
done < <(find "$DEST" -name '*.dump' -type f | sort)

echo "pulled to $DEST — $n dumps checked, $bad bad"
[ -f "$DEST/last_backup" ] && echo "server's last backup: $(cat "$DEST/last_backup")"
[ -f "$DEST/last_drill" ] && echo "server's last drill:  $(cat "$DEST/last_drill")"
[ "$bad" -eq 0 ]
