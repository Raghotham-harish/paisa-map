"""
test_legal_pages.py — the policy pages a payment gateway's website review looks for.

    python3 tests/test_legal_pages.py            # structure + routes (drafts pass)
    python3 tests/test_legal_pages.py --ready    # ALSO fails while any [[PLACEHOLDER]] is left

Run with --ready before pushing refund.html / contact.html: it is the gate that stops a page with
"[[ADDRESS]]" on it going live. Plain script, no server needed beyond Flask's test client.
"""
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "paisamap-etl", "etl"))
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/legal_pages.sqlite")
READY = "--ready" in sys.argv
passed = 0


def check(cond, msg):
    global passed
    assert cond, f"FAIL: {msg}"
    passed += 1


import server
c = server.app.test_client()
pages = {"/terms": "terms.html", "/privacy": "privacy.html", "/refund": "refund.html", "/contact": "contact.html"}
bodies = {}
for route, fname in pages.items():
    r = c.get(route)
    check(r.status_code == 200, f"{route} is served by Flask")
    bodies[route] = r.get_data(as_text=True)
    r.close()
    check(bodies[route] == open(os.path.join(REPO, fname), encoding="utf-8").read(), f"{route} serves {fname}")

# every page links to every other, so a reviewer (or a customer) can reach all four from any of them
for route, html in bodies.items():
    for other in pages:
        if other != route:
            check(f'href="{other}"' in html, f"{route} links to {other}")

refund = bodies["/refund"].lower()
for phrase, why in [("non-refundable", "states credits are non-refundable"), ("duplicate", "covers duplicate/failed charges"),
                    ("5–7 business days", "says how long refunds take"), ("original payment method", "says where refunds go"),
                    ("cancel", "explains cancellation"), ("7 days", "states the first-purchase window")]:
    check(phrase in refund, f"refund page {why}")
contact = bodies["/contact"].lower()
for phrase, why in [("mailto:", "has an email"), ("registered address", "has an address section"), ("grievance", "names a grievance officer"),
                    ("phone", "has a phone line")]:
    check(phrase in contact, f"contact page {why}")
for route in ("/refund", "/contact"):
    check("<title>" in bodies[route] and 'rel="canonical"' in bodies[route] and route in bodies[route], f"{route} has title + canonical")

left = {r: sorted(set(re.findall(r"\[\[[A-Z_]+\]\]", h))) for r, h in bodies.items() if re.search(r"\[\[[A-Z_]+\]\]", h)}
if READY:
    check(not left, f"no placeholders left, still to fill: {left}")
print(f"OK — {passed} checks passed" + ("" if READY else f"  (draft mode; placeholders still to fill: {left or 'none'})"))
