# apply_nginx_security_headers.sh — pen-test finding F4 (docs/PENTEST_CHECKLIST.md).
#
# Adds X-Frame-Options / X-Content-Type-Options / Referrer-Policy to everything nginx serves
# itself (/, /workspace/, /privacy, /terms) and hides Flask's duplicate copies on the three
# proxied /api/ blocks, so each header is sent exactly once. Backs the live config up to
# /etc/nginx/backups/, runs `nginx -t`, and RESTORES the backup automatically if the test fails.
#
# Run from your Mac (needs sudo on the server; the file travels over stdin, nothing is left there):
#   ssh -i ~/.ssh/paisamap_lightsail ubuntu@paisamaps.com 'sudo bash -s' < paisamap-etl/db/apply_nginx_security_headers.sh
#
# Afterwards:
#   curl -sI https://paisamaps.com/workspace/ | grep -i -E "x-frame|nosniff|referrer"   # want each exactly once
#   curl -sI https://paisamaps.com/api/health  | grep -i -E "x-frame|nosniff|referrer"   # want each exactly once (not doubled)
# and open /workspace/map in a browser — SAMEORIGIN still lets the workspace embed the map iframe.
# Rollback: cp -p /etc/nginx/backups/paisamaps.bak-20260918-preheaders /etc/nginx/sites-enabled/paisamaps && nginx -t && systemctl reload nginx
set -e
CONF=/etc/nginx/sites-enabled/paisamaps
BAK=/etc/nginx/backups/paisamaps.bak-20260918-preheaders
cp -p "$CONF" "$BAK"
python3 - <<'PY'
p = "/etc/nginx/sites-enabled/paisamaps"
s = open(p).read()
hsts = '    add_header Strict-Transport-Security "max-age=15768000" always;\n'
assert s.count(hsts) == 1 and "X-Frame-Options" not in s, "config is not in the expected shape - refusing to edit"
s = s.replace(hsts, hsts +
    '\n    # Security headers for everything nginx serves itself (added 2026-09-18, pen-test F4).\n'
    '    # Flask sets the same headers on /api/ responses; the /api/ blocks below hide\n'
    '    # its copies so each header is sent exactly once.\n'
    '    add_header X-Frame-Options "SAMEORIGIN" always;\n'
    '    add_header X-Content-Type-Options "nosniff" always;\n'
    '    add_header Referrer-Policy "strict-origin-when-cross-origin" always;\n'
    '    server_tokens off;\n', 1)
proxy = "        proxy_pass http://127.0.0.1:8080;\n"
assert s.count(proxy) == 3, "expected exactly three proxied /api/ blocks - refusing to edit"
s = s.replace(proxy, proxy +
    "        proxy_hide_header X-Frame-Options;\n"
    "        proxy_hide_header X-Content-Type-Options;\n"
    "        proxy_hide_header Referrer-Policy;\n")
open(p, "w").write(s)
PY
if nginx -t 2>&1; then
  systemctl reload nginx && echo "RELOADED"
else
  echo "nginx -t FAILED - restoring backup"; cp -p "$BAK" "$CONF"; nginx -t; exit 1
fi
