"""
subscription_sweep.py — takes the plan away from subscriptions that have ended
AND whose paid-for period is over (billing-v2 P2). Idempotent; the webhook and the
Billing page also call it opportunistically, so cron is only the backstop for a
company nobody has touched since its subscription ended.

    sudo bash -c 'set -a; . /etc/paisamap/db.env; set +a; cd /home/ubuntu/paisa-map; \\
      exec venv-flask/bin/python3 paisamap-etl/db/subscription_sweep.py'

Suggested cron (hourly):  17 * * * *  <the command above>
Prints only a count, never a connection string.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "etl"))

import _subscriptions as S  # noqa: E402

if not os.environ.get("DATABASE_URL"):
    sys.exit("DATABASE_URL is not set")

print(f"revoked plans for {S.sweep_ended()} ended subscription(s)")
