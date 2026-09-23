#!/bin/bash
# apply_nginx_policy_routes.sh — serve /refund and /contact from nginx, exactly like /privacy and /terms.
#
# nginx has no try_files fallback for extension-less paths, so a Flask route alone is not enough:
# without a `location = /refund { try_files /refund.html =404; }` block the page 404s at the nginx layer
# (same reason /privacy and /terms each have one). Backs the live config up to /etc/nginx/backups/, runs
# `nginx -t`, and RESTORES the backup automatically if the test fails. Safe to run twice (no-op the second time).
#
# Run from your Mac AFTER the deploy that carries refund.html / contact.html (the file travels over stdin,
# nothing is left on the server):
#   ssh -i ~/.ssh/paisamap_lightsail ubuntu@paisamaps.com 'sudo bash -s' < paisamap-etl/db/apply_nginx_policy_routes.sh
#
# Afterwards:
#   curl -s -o /dev/null -w "%{http_code}\n" https://paisamaps.com/refund    # want 200
#   curl -s -o /dev/null -w "%{http_code}\n" https://paisamaps.com/contact   # want 200
# Rollback: cp -p /etc/nginx/backups/paisamaps.bak-<timestamp>-prepolicy /etc/nginx/sites-enabled/paisamaps && nginx -t && systemctl reload nginx
set -e
CONF=/etc/nginx/sites-enabled/paisamaps
mkdir -p /etc/nginx/backups
BAK=/etc/nginx/backups/paisamaps.bak-$(date +%Y%m%d%H%M%S)-prepolicy
cp -p "$CONF" "$BAK"
python3 - <<'PY'
import re
p = "/etc/nginx/sites-enabled/paisamaps"
s = open(p).read()
if "location = /refund" in s and "location = /contact" in s:
    print("already present - nothing to do"); raise SystemExit(0)
m = re.search(r"(?m)^([ \t]*)location = /terms \{[^}]*\}\n", s)
assert m, "could not find the `location = /terms` block - refusing to edit"
ind = m.group(1)
def block(path):
    return f"{ind}location = /{path} {{\n{ind}    try_files /{path}.html =404;\n{ind}}}\n"
add = ""
for path in ("refund", "contact"):
    if f"location = /{path}" not in s:
        add += block(path)
s = s[:m.end()] + add + s[m.end():]
open(p, "w").write(s)
print("added:", [x for x in ("refund", "contact") if f"location = /{x} " in s])
PY
if nginx -t 2>&1; then
  systemctl reload nginx && echo "RELOADED"
else
  echo "nginx -t FAILED - restoring backup"; cp -p "$BAK" "$CONF"; nginx -t; exit 1
fi
