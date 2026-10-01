"""
subscription_sweep.py — the time-driven half of subscription billing (P2 + P13):
  * takes the plan away from subscriptions that have ended AND whose paid-for
    period is over;
  * emails the paying company once when a renewal has failed, and once more when
    the lock starts (the lock itself needs no job — it is computed at read time);
  * P4 free trials: emails "your trial ends tomorrow" once, marks converted trials
    ended, and takes a trial whose first charge never landed back to Free;
  * writes off plan credits that have expired (P3) in wallets nobody has looked
    at since — balance reads and spends also do this for their own wallet.
Idempotent, and each email is claimed with a conditional UPDATE so overlapping runs
can't double-send. The webhook also runs both opportunistically, so cron is the
backstop for a company nobody has touched since.

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

if not S.tables_ready():
    # Cron can be installed before the apply script has run: that is not an error.
    print("subscription tables not applied yet - nothing to do")
    sys.exit(0)

print(f"revoked plans for {S.sweep_ended()} ended subscription(s)")
print(f"sent {len(S.dunning_sweep())} dunning notice(s)")
print(f"sent {len(S.trial_sweep())} trial notice(s)")
print(f"wrote off {S.A.expire_due_credits()} expired plan credit(s)")
