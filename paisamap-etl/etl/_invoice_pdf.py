"""
_invoice_pdf.py — renders a single GST invoice to PDF.

Structurally much simpler than _report_pdf.py (one page, one line item, a tax
breakdown table) — reuses its color palette for brand consistency but doesn't
share layout code, since a report's per-location sections have nothing in
common with an invoice's header/line-item/tax-summary shape.

Carries what GST Rule 46 asks of a tax invoice: supplier name, address and
GSTIN; a serial number unique within the financial year; the date; the
recipient's name, address and GSTIN (when registered); SAC; taxable value; the
rate and amount of each tax head (CGST + SGST inside Karnataka, IGST outside);
place of supply with state name and code; whether tax is on reverse charge.
The rules themselves live in _gst.py.
"""

from datetime import timezone
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle

import _gst
from _report_pdf import RUPEE_DEEP, INK, INK_SOFT, BORDER, PAPER_2


def _styles():
    ss = getSampleStyleSheet()
    ss.add(ParagraphStyle("Brand", parent=ss["Normal"], fontName="Helvetica-Bold",
                           fontSize=14, textColor=RUPEE_DEEP, spaceAfter=2))
    ss.add(ParagraphStyle("InvoiceTitle", parent=ss["Title"], textColor=INK, fontSize=18,
                           alignment=0, spaceBefore=6, spaceAfter=6))
    ss.add(ParagraphStyle("Meta", parent=ss["Normal"], textColor=INK_SOFT, fontSize=9.5,
                           leading=13, spaceAfter=12))
    ss.add(ParagraphStyle("Block", parent=ss["Normal"], textColor=INK, fontSize=9.5,
                           leading=13))
    ss.add(ParagraphStyle("Words", parent=ss["Normal"], textColor=INK, fontSize=9.5,
                           leading=13, spaceBefore=8))
    ss.add(ParagraphStyle("Disclaimer", parent=ss["Normal"], textColor=INK_SOFT, fontSize=8,
                           leading=11, spaceBefore=24))
    return ss


def _fmt_amount(paise):
    return f"Rs. {paise / 100:,.2f}"


def _e(value):
    """Buyer-supplied text goes into reportlab's mini-markup: escape it."""
    return escape(str(value)) if value else ""


def _state_label(code):
    return f"{_gst.STATES.get(code, 'Unknown')} ({code})"


def _ist_date(value):
    if not hasattr(value, "strftime"):
        return str(value)
    if value.tzinfo is None:  # SQLite hands back naive UTC
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(_gst.IST).strftime("%d %b %Y")


def _seller_block(styles, seller_gstin):
    s = _gst.SELLER
    lines = ["<b>From</b>", f"<b>{s['trade_name']}</b> (proprietor: {s['legal_name']})",
             *s["address_lines"], f"State: {_state_label(s['state_code'])}"]
    lines.append(f"GSTIN: {seller_gstin}" if seller_gstin else "GSTIN: Not registered")
    lines.append(s["email"])
    return Paragraph("<br/>".join(lines), styles["Block"])


def _buyer_block(styles, invoice, details):
    lines = ["<b>Billed to</b>"]
    if invoice.get("buyer_name"):
        lines.append(f"<b>{_e(invoice['buyer_name'])}</b>")
    if details.get("buyer_address"):
        lines.append(_e(details["buyer_address"]))
    if details.get("buyer_state"):
        lines.append(f"State: {_state_label(details['buyer_state'])}")
    if invoice.get("buyer_gstin"):
        lines.append(f"GSTIN: {_e(invoice['buyer_gstin'])}")
    lines.append(_e(invoice.get("buyer_email")))
    return Paragraph("<br/>".join(lines), styles["Block"])


def _parties_table(styles, invoice, details):
    t = Table([[_seller_block(styles, invoice.get("seller_gstin")),
                _buyer_block(styles, invoice, details)]],
              colWidths=[85 * mm, 85 * mm])
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (0, 0), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 14),
    ]))
    return t


def _amounts_table(invoice, details):
    taxable = invoice["taxable_amount_paise"]
    rows = [
        ["#", "Description", "SAC", "Qty", "Amount"],
        ["1", Paragraph(_e(invoice["line_item_label"])), details.get("sac") or _gst.SAC_CODE,
         "1", _fmt_amount(taxable)],
    ]
    if invoice.get("seller_gstin") and invoice["gst_amount_paise"]:
        half = invoice["gst_rate"] * 100 / 2
        if details.get("igst_paise"):
            rows.append(["", f"IGST @ {invoice['gst_rate'] * 100:g}%", "", "",
                         _fmt_amount(details["igst_paise"])])
        else:
            rows.append(["", f"CGST @ {half:g}%", "", "", _fmt_amount(details.get("cgst_paise", 0))])
            rows.append(["", f"SGST @ {half:g}%", "", "", _fmt_amount(details.get("sgst_paise", 0))])
    rows.append(["", "Total", "", "", _fmt_amount(invoice["total_amount_paise"])])
    last = len(rows) - 1
    t = Table(rows, colWidths=[8 * mm, 88 * mm, 22 * mm, 12 * mm, 40 * mm])
    t.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME", (0, last), (-1, last), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9.5),
        ("TEXTCOLOR", (0, 0), (-1, 0), INK_SOFT),
        ("TEXTCOLOR", (0, 1), (-1, -1), INK),
        ("BACKGROUND", (0, 0), (-1, 0), PAPER_2),
        ("BACKGROUND", (0, last), (-1, last), PAPER_2),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("LINEBELOW", (0, 0), (-1, -1), 0.5, BORDER),
        ("ALIGN", (2, 0), (-1, -1), "RIGHT"),
    ]))
    return t


def build_invoice_pdf(invoice: dict, user: dict, invoices_dir: Path, details: dict = None) -> Path:
    """invoice: a dict row from _auth_db.create_invoice/get_invoice (has
    invoice_number, buyer_*, seller_gstin, taxable_amount_paise, gst_rate,
    gst_amount_paise, total_amount_paise, line_item_label, created_at). user:
    the buyer's users table row. details: what the invoices row doesn't hold,
    frozen on the order at checkout — place_of_supply, cgst/sgst/igst_paise,
    buyer_address (blueprints/billing.py:_create_invoice_for_order). Writes to
    invoices_dir/<user_id>/invoice_<invoice_number>.pdf, mirroring
    reports.py's REPORTS_DIR/<user_id>/report_<id>.pdf convention — must live
    outside the git-tracked tree deploy.sh rsyncs with --delete. Returns the
    written path."""
    details = dict(details or {})
    pos = details.get("place_of_supply") or _gst.SELLER["state_code"]
    if not any(details.get(k) for k in ("cgst_paise", "sgst_paise", "igst_paise")):
        details.update(_gst.split_gst(invoice["gst_amount_paise"], pos))

    user_dir = invoices_dir / str(user["id"])
    user_dir.mkdir(parents=True, exist_ok=True)
    out_path = user_dir / f"invoice_{invoice['invoice_number']}.pdf"

    registered = bool(invoice.get("seller_gstin"))
    styles = _styles()
    doc = SimpleDocTemplate(
        str(out_path), pagesize=A4,
        topMargin=20 * mm, bottomMargin=18 * mm, leftMargin=20 * mm, rightMargin=20 * mm,
        title=f"Invoice {invoice['invoice_number']}", author=_gst.SELLER["trade_name"],
    )
    meta = f"Invoice no. <b>{invoice['invoice_number']}</b> &middot; Date: {_ist_date(invoice['created_at'])}"
    if registered:
        meta += (f"<br/>Place of supply: {_state_label(pos)} &middot; "
                 "Tax payable on reverse charge: No")
    story = [
        Paragraph(_gst.SELLER["trade_name"], styles["Brand"]),
        # Only a GST-registered supplier issues a tax invoice.
        Paragraph("Tax Invoice" if registered else "Invoice", styles["InvoiceTitle"]),
        Paragraph(meta, styles["Meta"]),
        _parties_table(styles, invoice, details),
        _amounts_table(invoice, details),
        Paragraph(f"<b>Amount in words:</b> {_gst.amount_in_words(invoice['total_amount_paise'])}",
                  styles["Words"]),
        Spacer(1, 6 * mm),
        Paragraph(
            f"Amounts in INR. PaisaMap ({_gst.SELLER['website']}) is operated by "
            f"{_gst.SELLER['trade_name']}. This is a computer-generated invoice and does not "
            "require a signature.",
            styles["Disclaimer"],
        ),
    ]
    doc.build(story)
    return out_path
