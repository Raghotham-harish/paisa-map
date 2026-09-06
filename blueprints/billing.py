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
from pathlib import Path

from flask import Blueprint, request, jsonify, send_file

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "paisamap-etl" / "etl"))
import _pricing  # noqa: E402
import _invoice_pdf  # noqa: E402

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
    return jsonify(_pricing.public_pricing_payload())


def _create_razorpay_order_and_local_row(user_id, kind, amount_paise, **kwargs):
    client = _client()
    if client is None:
        return None, _not_configured()
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
    order = _auth_db.create_order(user_id, kind, rp_order["id"], amount_paise, **kwargs)
    return {
        "order": order,
        "razorpay_order_id": rp_order["id"],
        "razorpay_key_id": os.environ.get("RAZORPAY_KEY_ID"),
        "amount_paise": amount_paise,
        "currency": "INR",
    }, None


@billing_bp.route("/orders/credits", methods=["POST"])
@require_login
def create_credit_order(user_id):
    body = request.get_json(silent=True) or {}
    pack_id = body.get("pack_id")
    pack = _pricing.CREDIT_PACKS.get(pack_id)
    if pack is None:
        return jsonify({"error": "invalid_pack"}), 400
    payload, err = _create_razorpay_order_and_local_row(
        user_id, "credit_pack", pack["price_paise"], credit_pack_id=pack_id)
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
    payload, err = _create_razorpay_order_and_local_row(
        user_id, "plan_upgrade", plan_cfg["price_paise"], target_plan=target_plan)
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
    if project_id is None or _auth_db.get_project(project_id, user_id) is None:
        return jsonify({"error": "project not_found"}), 404
    payload, err = _create_razorpay_order_and_local_row(
        user_id, "report_purchase", _pricing.REPORT_PURCHASE_PRICE_PAISE, project_id=project_id)
    if err:
        return err
    return jsonify(payload), 201


def _create_invoice_for_order(order, line_item):
    user = _auth_db.get_user(order["user_id"])
    total = order["amount_paise"]
    taxable = round(total / (1 + _pricing.GST_RATE))
    gst = total - taxable
    invoice = _auth_db.create_invoice(
        order["id"], order["user_id"], buyer_email=user["email"], buyer_name=user["name"],
        taxable_amount_paise=taxable, gst_amount_paise=gst, total_amount_paise=total,
        line_item_label=line_item, seller_gstin=os.environ.get("PAISAMAP_GSTIN") or None,
    )
    path = _invoice_pdf.build_invoice_pdf(invoice, user, INVOICES_DIR)
    _auth_db.update_invoice_file_path(invoice["id"], str(path))


def _apply_paid_order(order, payment_id, signature):
    """Shared by /verify and /webhook. mark_order_paid's rowcount-based
    newly_paid flag is what makes this idempotent: a redelivered webhook after
    /verify already ran (or vice versa) is a safe no-op, not a double grant."""
    updated_order, newly_paid = _auth_db.mark_order_paid(order["id"], payment_id, signature)
    if newly_paid:
        if order["kind"] == "credit_pack":
            pack = _pricing.CREDIT_PACKS[order["credit_pack_id"]]
            _auth_db.grant_credits(order["user_id"], pack["credits"],
                                    reason="credit_purchase", ref_type="order", ref_id=order["id"])
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
    if payload.get("event") != "payment.captured":
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
