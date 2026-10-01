"""
billing.py — Phase 3 (Monetisation): Razorpay order creation, payment
verification/webhook, and invoice list/download.

No live Razorpay account exists yet (Phase 3 scoping decision) — every route
below degrades to a clean 503 "billing_unavailable" if RAZORPAY_KEY_ID/
RAZORPAY_KEY_SECRET aren't set, checked lazily per-request via _client(), not
at import time. This means the app boots fine and every other feature works
with zero Razorpay configuration; billing simply isn't usable until real (or
test-mode) keys are added to /etc/paisamap/db.env. Mirrors blueprints/auth.py's
existing GOOGLE_CLIENT_ID-unset handling.

Two ways a payment gets applied (both funnel into _apply_paid_order, and both
are idempotent via _auth_db.mark_order_paid's status='created' guard, since
Razorpay webhooks can be redelivered and can race the client-side call):
  - POST /verify — the primary path. Called by the frontend immediately after
    Razorpay Checkout's own success handler fires client-side. No public HTTPS
    endpoint is needed for this, which is what makes local/test-mode dev
    possible without exposing anything.
  - POST /webhook — server-to-server, no login (Razorpay calls this directly).
    The production-hardening belt-and-suspenders path for a payment whose
    browser tab closed before the client-side callback fired.
"""

import os
import sys
from datetime import timedelta
from pathlib import Path

from flask import Blueprint, request, jsonify, send_file

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "paisamap-etl" / "etl"))
import _pricing  # noqa: E402
import _gst  # noqa: E402
import _invoice_pdf  # noqa: E402
import _subscriptions  # noqa: E402
import _assisted_sale  # noqa: E402

from ._session import require_login, _auth_db  # noqa: E402

try:
    import razorpay
except ImportError:
    razorpay = None

billing_bp = Blueprint("billing", __name__, url_prefix="/api/billing")

INVOICES_DIR = Path(os.environ.get("INVOICES_DIR")
                     or (Path(__file__).resolve().parent.parent / "data" / "invoices"))


def _client():
    """Returns a configured razorpay.Client, or None if not configured. Every
    route below checks this and returns 503 rather than raising, so an
    unconfigured deploy degrades to "billing not configured" instead of
    crashing at import time or 500ing opaquely."""
    key_id = os.environ.get("RAZORPAY_KEY_ID")
    key_secret = os.environ.get("RAZORPAY_KEY_SECRET")
    if razorpay is None or not key_id or not key_secret:
        return None
    return razorpay.Client(auth=(key_id, key_secret))


def _not_configured():
    return jsonify({"error": "billing_unavailable",
                     "detail": "Payment gateway is not configured on this server."}), 503


@billing_bp.route("/pricing", methods=["GET"])
def pricing():
    payload = _pricing.public_pricing_payload()
    payload["checkout"] = checkout_state()
    payload["gst"] = {"charged": _seller_gstin() is not None, "rate": _pricing.GST_RATE,
                      "seller_state": _gst.SELLER["state_code"], "states": _gst.STATES}
    payload["subscriptions"] = subscriptions_state()
    return jsonify(payload)


def _seller_gstin():
    """PaisaMap's GSTIN if a valid one is configured, else None. Every list price
    is ex-GST; GST is added on top only when this returns a GSTIN, so GST is
    never collected unregistered. A malformed value fails closed (no GST)
    rather than printing a bad GSTIN on invoices."""
    value = (os.environ.get("PAISAMAP_GSTIN") or "").strip().upper()
    return value if _pricing.valid_gstin(value) else None


def _live_keys() -> bool:
    """True when the configured Razorpay key is a LIVE-mode key (real money). The
    mode is nothing but the key id's prefix; nothing else in the app knows it."""
    return (os.environ.get("RAZORPAY_KEY_ID") or "").startswith("rzp_live_")


def checkout_state() -> dict:
    """What may be bought right now. Test keys: everything, exactly as before.
    Live keys: the prices in _pricing.py's legacy block are placeholders (see the
    comment there), so nothing is purchasable until BILLING_LIVE_PURCHASES=1 is
    set deliberately, and the legacy Pro/Team plan checkout is never purchasable
    with live keys at all — v2 subscriptions replace it."""
    if not _live_keys():
        return {"live": False, "plans": True, "credits": True, "reports": True}
    open_ = os.environ.get("BILLING_LIVE_PURCHASES") == "1"
    return {"live": True, "plans": False, "credits": open_, "reports": open_}


_KIND_TO_GATE = {"plan_upgrade": "plans", "credit_pack": "credits", "report_purchase": "reports"}


def _purchase_gate(kind):
    """None if `kind` may be bought now, else a ready 403 response. Checked before
    Razorpay is contacted, so a refused purchase leaves no order behind."""
    if checkout_state().get(_KIND_TO_GATE.get(kind, ""), False):
        return None
    if kind == "plan_upgrade":
        return jsonify({"error": "plan_checkout_retired",
                        "detail": "Plans are set up with PaisaMap directly for now."}), 403
    return jsonify({"error": "purchases_not_open",
                    "detail": "Purchases aren't open yet. Ask PaisaMap and we'll add credits for you."}), 403


def _last_buyer(user_id, payer_org_id):
    """The billing details this person last gave when buying for this company,
    so they needn't retype their GSTIN every time. Read from past orders' meta
    (no separate table: an order is where the details are legally used)."""
    for order in _auth_db.list_orders(user_id):
        meta = order.get("meta")
        if not isinstance(meta, dict) or "buyer" not in meta:
            continue
        if (order.get("billing_org_id") or order.get("org_id")) == payer_org_id:
            return meta["buyer"]
    return None


def _checkout_buyer(user_id, body, payer_org_id):
    """(buyer, error_response). `billing` omitted = reuse the last details given
    for this company; `billing` sent (even empty) = use exactly that."""
    if "billing" not in body:
        return _last_buyer(user_id, payer_org_id), None
    buyer, code = _gst.normalize_buyer(body.get("billing"))
    if code:
        return None, (jsonify({"error": code}), 400)
    return buyer, None


def _tax_for(list_paise, buyer):
    """The GST split for one charge of `list_paise` (ex-GST), frozen onto the
    order (or subscription) at checkout: GST only when registered, SAC, place
    of supply from the buyer's details, CGST+SGST vs IGST heads."""
    gstin = _seller_gstin()
    tax = _pricing.gst_breakdown(list_paise, charge_gst=gstin is not None)
    tax["seller_gstin"] = gstin
    tax["sac"] = _gst.SAC_CODE
    tax["place_of_supply"] = _gst.place_of_supply(buyer)
    tax.update(_gst.split_gst(tax["gst_paise"], tax["place_of_supply"]))
    return tax


def _sub_amount(tier, cycle="monthly"):
    """One subscription cycle's charge right now: list + GST when registered."""
    return _pricing.subscription_amount_paise(tier, cycle, charge_gst=_seller_gstin() is not None)


def _create_razorpay_order_and_local_row(user_id, kind, list_paise, buyer=None, **kwargs):
    """`list_paise` is the ex-GST list price. The amount charged is that plus
    GST when registered; the split is frozen on the order (meta.tax) so the
    invoice matches what was actually paid even if PAISAMAP_GSTIN changes
    between checkout and payment. `buyer` (optional billing details) fixes the
    place of supply: CGST+SGST inside Karnataka, IGST outside it."""
    blocked = _purchase_gate(kind)
    if blocked is not None:
        return None, blocked
    client = _client()
    if client is None:
        return None, _not_configured()
    tax = _tax_for(list_paise, buyer)
    amount_paise = tax["total_paise"]
    try:
        rp_order = client.order.create({
            "amount": amount_paise, "currency": "INR",
            "notes": {"kind": kind, "user_id": str(user_id)},
        })
    except Exception as e:
        # Razorpay rejected the request (bad/expired keys, network failure) —
        # a real possibility until a live/test account with working keys
        # exists. Fail as clean JSON rather than an unhandled 500/HTML page,
        # matching this codebase's convention of never letting a request
        # crash opaquely.
        return None, (jsonify({"error": "gateway_error", "detail": str(e)}), 502)
    order = _auth_db.create_order(user_id, kind, rp_order["id"], amount_paise,
                                  meta={"tax": tax, "buyer": buyer}, **kwargs)
    return {
        "order": order,
        "razorpay_order_id": rp_order["id"],
        "razorpay_key_id": os.environ.get("RAZORPAY_KEY_ID"),
        "amount_paise": amount_paise,
        "taxable_paise": tax["taxable_paise"],
        "gst_paise": tax["gst_paise"],
        "place_of_supply": tax["place_of_supply"],
        "currency": "INR",
    }, None


def _purchase_wallet(user_id, body):
    """Optional "Buying for: <company>" — body.org_id. Omitted = the buyer's own
    primary company (unchanged behaviour). Returns (wallet_kwargs, error_response).
    Recording only: which company/wallet a purchase is against is stored on the
    order and the resulting ledger row; balances are still per-user."""
    requested = body.get("org_id")
    if requested is not None and (isinstance(requested, bool) or not isinstance(requested, int)):
        return None, (jsonify({"error": "invalid_org_id"}), 400)
    wallet = _auth_db.resolve_purchase_wallet(user_id, requested)
    if wallet is None:
        # Same 403 whether the company doesn't exist or the caller just isn't
        # an owner/admin of it — don't reveal which companies exist.
        return None, (jsonify({"error": "not_allowed",
                                "detail": "Only a company's owner or admin can buy for it."}), 403)
    return {"org_id": wallet["org_id"], "billing_org_id": wallet["billing_org_id"]}, None


def _unpaid_block(billing_org_id):
    """A ready 402 while the paying company has a failed, unpaid renewal (P13):
    no NEW credit purchases then, even though they'd be granted instantly —
    otherwise a broken payment method lets an account keep topping up for free
    until the next successful charge."""
    if billing_org_id is None or _auth_db.billing_scope() != "wallet":
        return None
    if _auth_db.wallet_dunning(billing_org_id) is None:
        return None
    return jsonify({"error": "unpaid_balance",
                    "detail": "The last subscription payment failed and is still unpaid. "
                              "Update the payment method to buy more credits."}), 402


@billing_bp.route("/orders/credits", methods=["POST"])
@require_login
def create_credit_order(user_id):
    body = request.get_json(silent=True) or {}
    pack_id = body.get("pack_id")
    pack = _pricing.CREDIT_PACKS.get(pack_id)
    if pack is None:
        return jsonify({"error": "invalid_pack"}), 400
    wallet, werr = _purchase_wallet(user_id, body)
    if werr:
        return werr
    unpaid = _unpaid_block(wallet["billing_org_id"])
    if unpaid:
        return unpaid
    buyer, berr = _checkout_buyer(user_id, body, wallet["billing_org_id"] or wallet["org_id"])
    if berr:
        return berr
    payload, err = _create_razorpay_order_and_local_row(
        user_id, "credit_pack", pack["price_paise"], buyer=buyer, credit_pack_id=pack_id, **wallet)
    if err:
        return err
    return jsonify(payload), 201


@billing_bp.route("/orders/plan", methods=["POST"])
@require_login
def create_plan_order(user_id):
    body = request.get_json(silent=True) or {}
    target_plan = body.get("plan")
    plan_cfg = _pricing.PLAN_PRICES.get(target_plan)
    if plan_cfg is None:
        return jsonify({"error": "invalid_plan"}), 400
    wallet, werr = _purchase_wallet(user_id, body)
    if werr:
        return werr
    buyer, berr = _checkout_buyer(user_id, body, wallet["billing_org_id"] or wallet["org_id"])
    if berr:
        return berr
    payload, err = _create_razorpay_order_and_local_row(
        user_id, "plan_upgrade", plan_cfg["price_paise"], buyer=buyer, target_plan=target_plan, **wallet)
    if err:
        return err
    return jsonify(payload), 201


@billing_bp.route("/orders/report", methods=["POST"])
@require_login
def create_report_order(user_id):
    """One-off report purchase — bypasses the credit balance entirely. body:
    {project_id}, matching POST /api/reports' own body shape so the frontend's
    inline "buy this report" flow can reuse the same project selection state."""
    body = request.get_json(silent=True) or {}
    project_id = body.get("project_id")
    project = _auth_db.get_project(project_id, user_id) if project_id is not None else None
    if project is None:
        return jsonify({"error": "project not_found"}), 404
    unpaid = _unpaid_block(_auth_db.get_payer_org_id(project.get("org_id")))
    if unpaid:
        return unpaid
    # A report is always for one project, so it's recorded against that
    # project's company — no "Buying for" choice to make.
    buyer, berr = _checkout_buyer(user_id, body, project.get("org_id"))
    if berr:
        return berr
    payload, err = _create_razorpay_order_and_local_row(
        user_id, "report_purchase", _pricing.REPORT_PURCHASE_PRICE_PAISE, buyer=buyer,
        project_id=project_id, org_id=project.get("org_id"))
    if err:
        return err
    return jsonify(payload), 201


def _order_tax(order):
    """The order's frozen tax split (taxable, gst, rate, seller GSTIN). Orders
    from before GST-on-top have none: their amount was GST-inclusive, so GST is
    carved out of it — and only if a GSTIN is configured, never otherwise."""
    total = order["amount_paise"]
    meta = order.get("meta")
    tax = meta.get("tax") if isinstance(meta, dict) else None
    if tax and tax.get("taxable_paise", -1) + tax.get("gst_paise", -1) == total:
        return tax["taxable_paise"], tax["gst_paise"], tax["gst_rate"], tax.get("seller_gstin")
    gstin = _seller_gstin()
    if gstin is None:
        return total, 0, 0.0, None
    taxable = (total * 100 * 2 + (100 + _pricing.GST_PERCENT)) // (2 * (100 + _pricing.GST_PERCENT))
    return taxable, total - taxable, _pricing.GST_RATE, gstin


def _order_supply(order, gst):
    """(buyer, place_of_supply, heads) frozen on the order at checkout. Orders
    from before the invoice engine have no buyer details: no address on record
    means the supplier's own state, i.e. CGST + SGST."""
    meta = order.get("meta") if isinstance(order.get("meta"), dict) else {}
    tax = meta.get("tax") or {}
    buyer = meta.get("buyer")
    pos = tax.get("place_of_supply") or _gst.place_of_supply(buyer)
    heads = _gst.split_gst(gst, pos)
    return buyer, pos, dict(heads, sac=tax.get("sac") or _gst.SAC_CODE)


def _create_invoice_for_order(order, line_item):
    user = _auth_db.get_user(order["user_id"])
    total = order["amount_paise"]
    taxable, gst, rate, gstin = _order_tax(order)
    buyer, pos, heads = _order_supply(order, gst)
    buyer = buyer or {}
    invoice = _auth_db.create_invoice(
        order["id"], order["user_id"], buyer_email=user["email"],
        buyer_name=buyer.get("name") or user["name"], buyer_gstin=buyer.get("gstin"),
        taxable_amount_paise=taxable, gst_amount_paise=gst, total_amount_paise=total,
        line_item_label=line_item, seller_gstin=gstin, gst_rate=rate,
    )
    details = {"buyer_address": buyer.get("address"), "buyer_state": buyer.get("state_code"),
               "place_of_supply": pos, **heads}
    path = _invoice_pdf.build_invoice_pdf(invoice, user, INVOICES_DIR, details)
    _auth_db.update_invoice_file_path(invoice["id"], str(path))


def _apply_paid_order(order, payment_id, signature):
    """Shared by /verify and /webhook. mark_order_paid's rowcount-based
    newly_paid flag is what makes this idempotent: a redelivered webhook after
    /verify already ran (or vice versa) is a safe no-op, not a double grant."""
    updated_order, newly_paid = _auth_db.mark_order_paid(order["id"], payment_id, signature)
    if newly_paid:
        if order["kind"] == "credit_pack":
            pack = _pricing.CREDIT_PACKS[order["credit_pack_id"]]
            # Stamped against the wallet the buyer chose at checkout (falls back to
            # the buyer's company for orders created before wallets existed).
            _auth_db.grant_credits(order["user_id"], pack["credits"],
                                    reason="credit_purchase", ref_type="order", ref_id=order["id"],
                                    org_id=order.get("billing_org_id") or order.get("org_id"))
            line_item = pack["label"]
        elif order["kind"] == "plan_upgrade":
            _auth_db.set_user_plan(order["user_id"], order["target_plan"])
            line_item = f"{_pricing.PLAN_PRICES[order['target_plan']]['label']} plan — monthly"
        elif order["kind"] == "report_purchase":
            # No side effect here — the report doesn't exist yet at
            # order-creation time. The frontend calls POST /api/reports with
            # order_id afterward (blueprints/reports.py), which links this
            # order to the resulting report via link_order_to_report. This
            # keeps the webhook/verify path fast and side-effect-free beyond
            # "mark paid" + invoicing.
            line_item = "Report purchase"
        else:
            line_item = "Purchase"
        _auth_db.log_activity(order["user_id"], "purchase", target_type="order",
                               target_id=order["id"], metadata={"kind": order["kind"]})
        _create_invoice_for_order(updated_order, line_item)
    return {"order": _auth_db.get_order(order["id"], order["user_id"]), "status": "paid"}


@billing_bp.route("/verify", methods=["POST"])
@require_login
def verify_payment(user_id):
    client = _client()
    if client is None:
        return _not_configured()
    body = request.get_json(silent=True) or {}
    order_id = body.get("razorpay_order_id")
    payment_id = body.get("razorpay_payment_id")
    signature = body.get("razorpay_signature")
    if not (order_id and payment_id and signature):
        return jsonify({"error": "missing_fields"}), 400

    try:
        client.utility.verify_payment_signature({
            "razorpay_order_id": order_id,
            "razorpay_payment_id": payment_id,
            "razorpay_signature": signature,
        })
    except razorpay.errors.SignatureVerificationError:
        return jsonify({"error": "signature_invalid"}), 400

    order = _auth_db.get_order_by_razorpay_id(order_id)
    if order is None or order["user_id"] != user_id:
        return jsonify({"error": "order_not_found"}), 404

    result = _apply_paid_order(order, payment_id, signature)
    return jsonify(result), 200


@billing_bp.route("/webhook", methods=["POST"])
def webhook():
    """No @require_login — Razorpay calls this server-to-server, no browser
    session cookie is involved."""
    secret = os.environ.get("RAZORPAY_WEBHOOK_SECRET")
    if not secret or razorpay is None:
        return _not_configured()
    signature = request.headers.get("X-Razorpay-Signature", "")
    raw_body = request.get_data(as_text=True)
    try:
        # Utility.verify_webhook_signature is an instance method — it must be
        # instantiated, not called on the class directly (that silently binds
        # the wrong argument to `self` instead of raising a clear error, since
        # Python allows calling an unbound method this way). No client
        # needed for a pure local HMAC check.
        razorpay.Utility(None).verify_webhook_signature(raw_body, signature, secret)
    except razorpay.errors.SignatureVerificationError:
        return jsonify({"error": "signature_invalid"}), 400

    payload = request.get_json(silent=True) or {}
    event = payload.get("event") or ""
    if event.startswith("subscription."):
        return _handle_subscription_event(payload)
    if event != "payment.captured":
        return jsonify({"status": "ignored"}), 200

    payment_entity = payload["payload"]["payment"]["entity"]
    order = _auth_db.get_order_by_razorpay_id(payment_entity["order_id"])
    if order is None:
        return jsonify({"status": "unknown_order"}), 200
    # This path has no Checkout-issued razorpay_signature (that's a client-side
    # value from the /verify flow, not part of the webhook payload) — the
    # webhook's own X-Razorpay-Signature just verified above is a different
    # value (an HMAC over this whole payload) and would be misleading stored
    # in that column, so pass None rather than conflate the two.
    _apply_paid_order(order, payment_entity["id"], None)
    return jsonify({"status": "ok"}), 200


@billing_bp.route("/buyer", methods=["GET"])
@require_login
def get_buyer(user_id):
    """The billing details (name, GSTIN, state, address) this person last gave
    when buying for a company — only their own past orders, so nothing leaks.
    ?org_id= picks the company, as "Buying for" does at checkout."""
    raw = request.args.get("org_id")
    body = {}
    if raw is not None:
        if not raw.isdigit():
            return jsonify({"error": "invalid_org_id"}), 400
        body["org_id"] = int(raw)
    wallet, werr = _purchase_wallet(user_id, body)
    if werr:
        return werr
    return jsonify({"buyer": _last_buyer(user_id, wallet["billing_org_id"] or wallet["org_id"])})


@billing_bp.route("/invoices", methods=["GET"])
@require_login
def list_invoices(user_id):
    return jsonify({"invoices": _auth_db.list_invoices(user_id)})


@billing_bp.route("/invoices/<int:invoice_id>/download", methods=["GET"])
@require_login
def download_invoice(user_id, invoice_id):
    invoice = _auth_db.get_invoice(invoice_id, user_id)
    if invoice is None or not invoice.get("file_path"):
        return jsonify({"error": "not_found"}), 404
    path = Path(invoice["file_path"])
    if not path.exists():
        return jsonify({"error": "file_missing"}), 404
    return send_file(str(path), mimetype="application/pdf", as_attachment=True,
                      download_name=f"invoice_{invoice['invoice_number']}.pdf")


# ── Recurring subscriptions (billing-v2 P2) ──────────────────────────────────
# Dark by default: nothing here is reachable until BILLING_SUBSCRIPTIONS=1. The
# webhook side is NOT gated — an event for a subscription we know about must
# always be applied, or flipping the flag off would freeze in-flight state.

def subscriptions_state() -> dict:
    """Whether a customer may START a subscription right now, and why not.
    Needs wallet billing scope: the plan a subscription grants lives on the
    company, and only wallet scope reads it — without that a customer would pay
    and get nothing. Live keys additionally need BILLING_LIVE_PURCHASES=1, the
    same deliberate switch that guards every other real-money purchase."""
    if os.environ.get("BILLING_SUBSCRIPTIONS") != "1":
        return {"enabled": False, "reason": "not_enabled"}
    if _auth_db.billing_scope() != "wallet":
        return {"enabled": False, "reason": "wallet_scope_required"}
    if _live_keys() and os.environ.get("BILLING_LIVE_PURCHASES") != "1":
        return {"enabled": False, "reason": "purchases_not_open"}
    if not _subscriptions.tables_ready():
        return {"enabled": False, "reason": "schema_not_applied"}
    return {"enabled": True, "reason": None}


def _subscriptions_gate():
    state = subscriptions_state()
    if state["enabled"]:
        return None
    return jsonify({"error": "subscriptions_unavailable", "reason": state["reason"],
                    "detail": "Subscriptions aren't open yet."}), 403


def _payer_for_subscription(user_id, body_or_args):
    """The PAYING company a subscription attaches to, and only if the caller is
    its owner or admin. The plan lives on the payer (it's what every linked
    company's members inherit), so being admin of a linked client company is
    not enough. Returns (payer_org_id, error_response)."""
    requested = body_or_args.get("org_id")
    if isinstance(requested, str) and requested.isdigit():
        requested = int(requested)
    if requested is not None and (isinstance(requested, bool) or not isinstance(requested, int)):
        return None, (jsonify({"error": "invalid_org_id"}), 400)
    wallet = _auth_db.resolve_purchase_wallet(user_id, requested)
    payer = wallet["billing_org_id"] if wallet else None
    if wallet is not None and payer is None:
        return None, (jsonify({"error": "no_company"}), 409)
    if wallet is None or _auth_db.get_org_role(payer, user_id) not in ("owner", "admin"):
        return None, (jsonify({"error": "not_allowed",
                               "detail": "Only the paying company's owner or admin can manage its subscription."}), 403)
    return payer, None


def _ensure_razorpay_plan(client, tier, cycle, amount):
    """A Razorpay plan charging exactly `amount` per cycle (cached by amount, so
    GST on/off or a price change gets its own plan)."""
    plan_id = _subscriptions.cached_plan_id(tier, cycle, amount)
    if plan_id:
        return plan_id
    label = _pricing.TIERS[tier]["label"]
    rp_plan = client.plan.create({
        "period": "monthly", "interval": 1,
        "item": {"name": f"PaisaMap {label} (monthly)", "amount": amount, "currency": "INR"},
        "notes": {"tier": tier, "price_book": _pricing.PRICE_BOOK_VERSION},
    })
    return _subscriptions.save_plan_id(tier, cycle, amount, rp_plan["id"])


@billing_bp.route("/subscriptions", methods=["GET"])
@require_login
def get_subscription(user_id):
    payer, err = _payer_for_subscription(user_id, request.args)
    if err:
        return err
    ready = _subscriptions.tables_ready()
    if ready:
        _subscriptions.sweep_ended()
        _subscriptions.trial_sweep(background=True)
    trial_reason = _subscriptions.trial_unavailable(payer, user_id) if ready else "schema_not_applied"
    trial_tier = _pricing.TRIAL_CONVERTS_TO
    return jsonify({
        "state": subscriptions_state(),
        "standing": _auth_db.account_standing(user_id, payer) if ready else None,
        # A plan PaisaMap set up directly (invoice/NEFT): shown as "managed by PaisaMap until ...".
        "assisted": _assisted_sale.public_view(_assisted_sale.active_for_org(payer)) if ready else None,
        "subscription": _subscriptions.public_view(_subscriptions.get_latest(payer)) if ready else None,
        "options": [{"tier": t, "label": _pricing.TIERS[t]["label"],
                     "list_paise": _pricing.subscription_list_paise(t), "amount_paise": _sub_amount(t),
                     "credits_per_month": _pricing.TIERS[t]["credits_per_month"], "seats": _pricing.TIERS[t]["seats"]}
                    for t in _pricing.TIERS if _pricing.subscription_error(t) is None],
        # P4: may this person start the free trial for this company (card/mandate now,
        # first charge after TRIAL_DAYS)? `reason` says why not.
        "trial": {"available": trial_reason is None, "reason": trial_reason,
                  "days": _pricing.TRIAL_DAYS, "credits": _pricing.TRIAL_CREDITS,
                  "tier": trial_tier, "label": _pricing.TIERS[trial_tier]["label"],
                  "amount_paise": _sub_amount(trial_tier)},
    })


@billing_bp.route("/subscriptions", methods=["POST"])
@require_login
def create_subscription(user_id):
    blocked = _subscriptions_gate()
    if blocked is not None:
        return blocked
    body = request.get_json(silent=True) or {}
    tier, cycle = body.get("tier"), body.get("cycle") or "monthly"
    trial = body.get("trial") is True
    bad = _pricing.subscription_error(tier, cycle)
    if bad:
        return jsonify({"error": bad}), 400
    if trial and tier != _pricing.TRIAL_CONVERTS_TO:
        return jsonify({"error": "trial_tier",
                        "detail": f"The free trial leads into {_pricing.TIERS[_pricing.TRIAL_CONVERTS_TO]['label']}."}), 400
    payer, err = _payer_for_subscription(user_id, body)
    if err:
        return err
    live = _subscriptions.get_live(payer)
    lapsed_trial = (live is not None and live.get("trial_ended_at") is not None
                    and not (live["paid_count"] or 0))
    if live and not lapsed_trial:
        return jsonify({"error": "already_subscribed"}), 409
    if trial:
        why = _subscriptions.trial_unavailable(payer, user_id)
        if why:
            return jsonify({"error": "trial_not_available", "reason": why}), 409
    held = _subscriptions.org_plan(payer)
    if (_pricing.parse_plan(held)[0] == "v2"
            and _pricing.tier_rank(held) >= _pricing.tier_rank(_pricing.plan_id_v2(tier))):
        return jsonify({"error": "plan_managed_directly",
                        "detail": "This company already has this plan or a higher one, set up with PaisaMap directly."}), 409
    buyer, berr = _checkout_buyer(user_id, body, payer)
    if berr:
        return berr
    client = _client()
    if client is None:
        return _not_configured()
    if lapsed_trial:
        # A trial whose first charge failed still sits "live" at Razorpay (retrying).
        # Subscribing again replaces it, so the old mandate can't charge as well.
        try:
            client.subscription.cancel(live["razorpay_subscription_id"], {"cancel_at_cycle_end": 0})
        except Exception as e:
            return jsonify({"error": "gateway_error", "detail": str(e)}), 502
        _subscriptions.record_state(live["razorpay_subscription_id"], status="cancelled",
                                    ends_at=_subscriptions._now())
        _subscriptions.sweep_ended()
    return _start_checkout(client, payer, user_id, tier, cycle, buyer, trial=trial)


def _sub_meta(tier, cycle, buyer):
    """What a subscription freezes at start (see subscriptions.meta)."""
    return {"tax": _tax_for(_pricing.subscription_list_paise(tier, cycle), buyer), "buyer": buyer}


def _start_checkout(client, payer, user_id, tier, cycle, buyer=None, trial=False):
    """Create the Razorpay subscription, record it locally (status 'created', grants
    nothing) and return what Checkout needs. A trial schedules the first charge
    TRIAL_DAYS out (start_at), so Checkout only authenticates the card/mandate."""
    meta = _sub_meta(tier, cycle, buyer)
    amount = meta["tax"]["total_paise"]
    ends = _subscriptions.trial_ends_at() if trial else None
    payload = {
        "plan_id": None, "total_count": _pricing.SUBSCRIPTION_TOTAL_COUNT,
        "quantity": 1, "customer_notify": 1,
        "notes": {"org_id": str(payer), "user_id": str(user_id), "tier": tier, "cycle": cycle},
    }
    if ends is not None:
        payload["start_at"] = int(ends.timestamp())
        payload["notes"]["trial"] = "1"
    try:
        plan_id = _ensure_razorpay_plan(client, tier, cycle, amount)
        payload["plan_id"] = plan_id
        rp_sub = client.subscription.create(payload)
    except Exception as e:
        return jsonify({"error": "gateway_error", "detail": str(e)}), 502
    _subscriptions.create_local(payer, user_id, tier, cycle, rp_sub["id"], plan_id, amount, meta=meta,
                                trial_ends_at=ends)
    return jsonify({
        "razorpay_subscription_id": rp_sub["id"],
        "razorpay_key_id": os.environ.get("RAZORPAY_KEY_ID"),
        "amount_paise": amount,
        "currency": "INR", "tier": tier,
        "trial_ends_at": ends.isoformat() if ends else None,
    }), 201


@billing_bp.route("/subscriptions/recover", methods=["POST"])
@require_login
def recover_subscription(user_id):
    """Fix a failed renewal with a NEW payment method. Razorpay can't swap the
    mandate on a subscription that is failing, so the failing one is retired and
    a fresh one for the same tier goes to Checkout. The replacement is started
    first, so if Razorpay refuses it the old subscription is untouched. The old
    one keeps its access until the original lock time (it never shortens what
    was already granted) and a paid replacement re-grants the plan."""
    blocked = _subscriptions_gate()
    if blocked is not None:
        return blocked
    body = request.get_json(silent=True) or {}
    payer, err = _payer_for_subscription(user_id, body)
    if err:
        return err
    sub = _subscriptions.get_live(payer)
    if sub is None or not sub["past_due_since"] or sub["status"] not in ("pending", "halted"):
        return jsonify({"error": "not_past_due"}), 409
    client = _client()
    if client is None:
        return _not_configured()
    # The replacement keeps the buyer details the failing one was started with.
    old_buyer = (sub.get("meta") or {}).get("buyer") if isinstance(sub.get("meta"), dict) else None
    meta = _sub_meta(sub["tier"], sub["cycle"], old_buyer)
    amount = meta["tax"]["total_paise"]
    try:
        plan_id = _ensure_razorpay_plan(client, sub["tier"], sub["cycle"], amount)
        rp_sub = client.subscription.create({
            "plan_id": plan_id, "total_count": _pricing.SUBSCRIPTION_TOTAL_COUNT,
            "quantity": 1, "customer_notify": 1,
            "notes": {"org_id": str(payer), "user_id": str(user_id), "tier": sub["tier"],
                      "cycle": sub["cycle"], "replaces": sub["razorpay_subscription_id"]},
        })
        client.subscription.cancel(sub["razorpay_subscription_id"], {"cancel_at_cycle_end": 0})
    except Exception as e:
        return jsonify({"error": "gateway_error", "detail": str(e)}), 502
    lock_at = _subscriptions._aware(sub["past_due_since"]) + timedelta(days=_pricing.DUNNING_DAYS)
    _subscriptions.record_state(sub["razorpay_subscription_id"], status="cancelled", ends_at=lock_at)
    _subscriptions.create_local(payer, user_id, sub["tier"], sub["cycle"], rp_sub["id"], plan_id,
                                amount, meta=meta)
    _subscriptions.sweep_ended()
    return jsonify({
        "razorpay_subscription_id": rp_sub["id"],
        "razorpay_key_id": os.environ.get("RAZORPAY_KEY_ID"),
        "amount_paise": amount,
        "currency": "INR", "tier": sub["tier"],
    }), 201


def _ensure_subscription_invoice(order, tier):
    if _subscriptions.invoice_exists(order["id"]):
        return
    from sqlalchemy.exc import IntegrityError
    try:
        _create_invoice_for_order(order, f"{_pricing.TIERS[tier]['label']} plan — monthly subscription")
    except IntegrityError:
        pass    # a concurrent delivery created it first


def _charge_meta(sub, charged_paise):
    """The tax split + buyer a charge's order (and so its invoice) carries: the
    subscription's frozen ones — but the split only when Razorpay charged exactly
    the amount it was frozen for. Anything else (a plan changed on Razorpay's
    side) is left to the carve-out in _order_tax rather than printing heads
    that don't add up to what was paid."""
    meta = sub.get("meta") if isinstance(sub.get("meta"), dict) else {}
    out = {"buyer": meta.get("buyer")}
    tax = meta.get("tax")
    if isinstance(tax, dict) and tax.get("total_paise") == charged_paise:
        out["tax"] = tax
    return out


def _finish_charge(sub, payment, sub_entity, event_ts=None):
    """One captured subscription payment, from /verify or the webhook: record the
    order, grant the plan, make sure an invoice exists. Idempotent end to end."""
    res = _subscriptions.apply_charge(
        sub["razorpay_subscription_id"], payment["id"], payment["amount"],
        order_meta=_charge_meta(sub, payment["amount"]),
        current_start=_subscriptions.ts(sub_entity.get("current_start")),
        current_end=_subscriptions.ts(sub_entity.get("current_end")),
        paid_count=sub_entity.get("paid_count"), event_ts=event_ts,
        razorpay_order_id=payment.get("order_id"))
    if "error" in res:
        return res
    _ensure_subscription_invoice(res["order"], sub["tier"])
    if res.get("recovered"):
        _subscriptions.notify_restored(sub["org_id"], background=True)
    if res["newly_paid"]:
        _auth_db.log_activity(res["order"]["user_id"], "subscription_charge", target_type="order",
                              target_id=res["order"]["id"],
                              metadata={"tier": sub["tier"], "org_id": sub["org_id"]})
    return res


@billing_bp.route("/subscriptions/verify", methods=["POST"])
@require_login
def verify_subscription(user_id):
    """Called right after Checkout's success handler, like /verify for orders.
    The signature proves Razorpay tied this payment to this subscription; the
    payment and subscription are then fetched from Razorpay, so what gets
    recorded is Razorpay's own account of the charge, not the browser's."""
    client = _client()
    if client is None:
        return _not_configured()
    body = request.get_json(silent=True) or {}
    sid, pid, sig = (body.get("razorpay_subscription_id"), body.get("razorpay_payment_id"),
                     body.get("razorpay_signature"))
    if not (sid and pid and sig):
        return jsonify({"error": "missing_fields"}), 400
    try:
        client.utility.verify_subscription_payment_signature({
            "razorpay_subscription_id": sid, "razorpay_payment_id": pid, "razorpay_signature": sig})
    except razorpay.errors.SignatureVerificationError:
        return jsonify({"error": "signature_invalid"}), 400
    sub = _subscriptions.get_by_razorpay_id(sid) if _subscriptions.tables_ready() else None
    if sub is None or _auth_db.get_org_role(sub["org_id"], user_id) not in ("owner", "admin"):
        return jsonify({"error": "subscription_not_found"}), 404
    try:
        payment = client.payment.fetch(pid)
        sub_entity = client.subscription.fetch(sid)
    except Exception as e:
        return jsonify({"error": "gateway_error", "detail": str(e)}), 502
    if not (sub_entity.get("paid_count") or 0):
        # No cycle has been charged: this payment only AUTHENTICATED the card/mandate
        # (a trial's token amount, refunded by Razorpay). Never treat it as a charge —
        # it would grant a paid month for a few rupees. A trial starts here instead.
        if sub_entity.get("status") == "authenticated":
            _subscriptions.record_state(sid, status="authenticated")
            if sub.get("trial_ends_at"):
                res = _subscriptions.start_trial(sid)
                if res.get("subscription") and res["subscription"].get("trial_started_at"):
                    return jsonify({"status": "trial",
                                    "subscription": _subscriptions.public_view(res["subscription"])}), 200
        return jsonify({"status": "pending"}), 200
    if payment.get("status") != "captured":
        # Not settled yet; the webhook finishes it when it is.
        return jsonify({"status": "pending"}), 200
    res = _finish_charge(sub, payment, sub_entity)
    if "error" in res:
        return jsonify({"error": res["error"]}), 409
    return jsonify({"status": "active",
                    "subscription": _subscriptions.public_view(res["subscription"])}), 200


@billing_bp.route("/subscriptions/cancel", methods=["POST"])
@require_login
def cancel_subscription(user_id):
    """Cancel keeps access to the end of the paid cycle (no partial-period
    refund, same as the proposed refund policy). A subscription that never got
    a paid cycle (pending/halted/authenticated) has nothing to keep, so that
    ends immediately."""
    body = request.get_json(silent=True) or {}
    payer, err = _payer_for_subscription(user_id, body)
    if err:
        return err
    sub = _subscriptions.get_live(payer) if _subscriptions.tables_ready() else None
    if sub is None:
        return jsonify({"error": "no_subscription"}), 404
    client = _client()
    if client is None:
        return _not_configured()
    at_cycle_end = sub["status"] == "active"
    try:
        client.subscription.cancel(sub["razorpay_subscription_id"],
                                   {"cancel_at_cycle_end": 1 if at_cycle_end else 0})
    except Exception as e:
        return jsonify({"error": "gateway_error", "detail": str(e)}), 502
    if at_cycle_end:
        res = _subscriptions.record_state(sub["razorpay_subscription_id"], cancel_at_period_end=True)
    else:
        # A running trial keeps its trial to the end (nothing will be charged); the
        # trial sweep takes it back to Free then. Anything else unpaid ends now.
        running_trial = sub.get("trial_started_at") and not sub.get("trial_ended_at")
        res = _subscriptions.record_state(
            sub["razorpay_subscription_id"], status="cancelled",
            ends_at=_subscriptions._aware(sub["trial_ends_at"]) if running_trial else _subscriptions._now())
        _subscriptions.sweep_ended()
        _subscriptions.trial_sweep(background=True)
    return jsonify({"subscription": _subscriptions.public_view(res.get("subscription"))}), 200


_EVENT_STATUS = {
    "subscription.authenticated": "authenticated", "subscription.activated": "active",
    "subscription.pending": "pending", "subscription.halted": "halted",
    "subscription.cancelled": "cancelled", "subscription.completed": "completed",
    "subscription.expired": "expired", "subscription.paused": "paused",
    "subscription.resumed": "active",
}


def _handle_subscription_event(payload):
    """subscription.* webhooks (signature already verified by the caller). Always
    answers 200 for anything well-formed — including subscriptions we don't know,
    which are simply not ours — so Razorpay doesn't retry forever."""
    event = payload.get("event")
    try:
        entity = payload["payload"]["subscription"]["entity"]
        sid = entity["id"]
    except (KeyError, TypeError):
        return jsonify({"status": "ignored"}), 200
    sub = _subscriptions.get_by_razorpay_id(sid) if _subscriptions.tables_ready() else None
    if sub is None:
        return jsonify({"status": "unknown_subscription"}), 200
    event_ts = _subscriptions.ts(payload.get("created_at"))
    if event == "subscription.charged":
        payment = ((payload.get("payload") or {}).get("payment") or {}).get("entity") or {}
        if payment.get("status") != "captured" or not payment.get("id"):
            return jsonify({"status": "ignored"}), 200
        _finish_charge(sub, payment, entity, event_ts)
    elif event in _EVENT_STATUS:
        state = _subscriptions.record_state(
            sid, status=_EVENT_STATUS[event], event_ts=event_ts,
            current_start=_subscriptions.ts(entity.get("current_start")),
            current_end=_subscriptions.ts(entity.get("current_end")),
            paid_count=entity.get("paid_count"))
        row = state.get("subscription") or {}
        if event == "subscription.authenticated" and row.get("trial_ends_at") and row.get("status") == "authenticated":
            _subscriptions.start_trial(sid)
    else:
        return jsonify({"status": "ignored"}), 200
    _subscriptions.sweep_ended()
    _subscriptions.trial_sweep(background=True)
    _subscriptions.dunning_sweep(background=True)
    return jsonify({"status": "ok"}), 200
