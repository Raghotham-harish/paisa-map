"""
_invoice_pdf.py — renders a single GST invoice to PDF.

Structurally much simpler than _report_pdf.py (one page, one line item, a tax
breakdown table) — reuses its color palette for brand consistency but doesn't
share layout code, since a report's per-location sections have nothing in
common with an invoice's header/line-item/tax-summary shape.
"""

from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle

from _report_pdf import RUPEE_DEEP, INK, INK_SOFT, BORDER, PAPER_2


def _styles():
    ss = getSampleStyleSheet()
    ss.add(ParagraphStyle("Brand", parent=ss["Normal"], fontName="Helvetica-Bold",
                           fontSize=14, textColor=RUPEE_DEEP, spaceAfter=2))
    ss.add(ParagraphStyle("InvoiceTitle", parent=ss["Title"], textColor=INK, fontSize=18,
                           spaceAfter=4))
    ss.add(ParagraphStyle("Meta", parent=ss["Normal"], textColor=INK_SOFT, fontSize=9.5,
                           spaceAfter=10))
    ss.add(ParagraphStyle("BlockLabel", parent=ss["Normal"], fontName="Helvetica-Bold",
                           textColor=INK_SOFT, fontSize=8.5, spaceAfter=2))
    ss.add(ParagraphStyle("Block", parent=ss["Normal"], textColor=INK, fontSize=10,
                           leading=14, spaceAfter=16))
    ss.add(ParagraphStyle("Disclaimer", parent=ss["Normal"], textColor=INK_SOFT, fontSize=8,
                           leading=11, spaceBefore=24))
    return ss


def _fmt_amount(paise):
    return f"Rs. {paise / 100:,.2f}"


def _party_block(styles, label, name, email, gstin):
    lines = [f"<b>{label}</b>"]
    if name:
        lines.append(name)
    if email:
        lines.append(email)
    lines.append(f"GSTIN: {gstin}" if gstin else "GSTIN: Not yet registered")
    return Paragraph("<br/>".join(lines), styles["Block"])


def _line_item_table(invoice):
    rows = [
        ["Description", "Taxable amount", f"GST ({invoice['gst_rate'] * 100:g}%)", "Total"],
        [invoice["line_item_label"],
         _fmt_amount(invoice["taxable_amount_paise"]),
         _fmt_amount(invoice["gst_amount_paise"]),
         _fmt_amount(invoice["total_amount_paise"])],
    ]
    t = Table(rows, colWidths=[60 * mm, 40 * mm, 35 * mm, 35 * mm])
    t.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME", (0, 1), (-1, 1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 9.5),
        ("TEXTCOLOR", (0, 0), (-1, 0), INK_SOFT),
        ("TEXTCOLOR", (0, 1), (-1, 1), INK),
        ("BACKGROUND", (0, 0), (-1, 0), PAPER_2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("LINEBELOW", (0, 0), (-1, -1), 0.5, BORDER),
        ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
    ]))
    return t


def build_invoice_pdf(invoice: dict, user: dict, invoices_dir: Path) -> Path:
    """invoice: a dict row from _auth_db.create_invoice/get_invoice (has
    invoice_number, buyer_*, seller_gstin, taxable_amount_paise, gst_rate,
    gst_amount_paise, total_amount_paise, line_item_label, created_at). user:
    the buyer's users table row. Writes to
    invoices_dir/<user_id>/invoice_<invoice_number>.pdf, mirroring
    reports.py's REPORTS_DIR/<user_id>/report_<id>.pdf convention — must live
    outside the git-tracked tree deploy.sh rsyncs with --delete. Returns the
    written path."""
    user_dir = invoices_dir / str(user["id"])
    user_dir.mkdir(parents=True, exist_ok=True)
    out_path = user_dir / f"invoice_{invoice['invoice_number']}.pdf"

    styles = _styles()
    doc = SimpleDocTemplate(
        str(out_path), pagesize=A4,
        topMargin=22 * mm, bottomMargin=18 * mm, leftMargin=20 * mm, rightMargin=20 * mm,
    )
    story = [
        Paragraph("PaisaMap", styles["Brand"]),
        Paragraph("Tax Invoice", styles["InvoiceTitle"]),
        Paragraph(
            f"Invoice {invoice['invoice_number']} &middot; "
            f"{invoice['created_at'].strftime('%d %b %Y') if hasattr(invoice['created_at'], 'strftime') else invoice['created_at']}",
            styles["Meta"],
        ),
        _party_block(styles, "From", "PaisaMap", None, invoice.get("seller_gstin")),
        _party_block(styles, "Billed to", invoice.get("buyer_name"), invoice.get("buyer_email"),
                     invoice.get("buyer_gstin")),
        _line_item_table(invoice),
        Spacer(1, 8 * mm),
        Paragraph(
            "Amounts in INR. This is a computer-generated invoice and does not "
            "require a signature.",
            styles["Disclaimer"],
        ),
    ]
    doc.build(story)
    return out_path
