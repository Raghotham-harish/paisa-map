"""
_report_pdf.py — renders a project's location-intelligence report to PDF.

Takes plain dicts (a project row + a list of blueprints.intelligence payloads,
already computed via compute_location_intelligence_batch) and lays them out
with reportlab. No data is computed here — this is presentation only, so the
PDF and the JSON the workspace renders on-screen always agree.
"""

from datetime import datetime, timezone

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable,
)

RUPEE = colors.HexColor("#216A0B")
RUPEE_DEEP = colors.HexColor("#115000")
INK = colors.HexColor("#1A1C1A")
INK_SOFT = colors.HexColor("#41493B")
BORDER = colors.HexColor("#C7CCC2")
PAPER_2 = colors.HexColor("#F3F4EF")
FLAME = colors.HexColor("#BA1A1A")
AMBER = colors.HexColor("#DFAE3A")

RISK_COLOR = {"Low": RUPEE_DEEP, "Medium": AMBER, "High": FLAME, "Unknown": INK_SOFT}


def _styles():
    ss = getSampleStyleSheet()
    ss.add(ParagraphStyle("Brand", parent=ss["Normal"], fontName="Helvetica-Bold",
                           fontSize=14, textColor=RUPEE_DEEP, spaceAfter=2))
    ss.add(ParagraphStyle("ReportTitle", parent=ss["Title"], textColor=INK, fontSize=20,
                           spaceAfter=4))
    ss.add(ParagraphStyle("Meta", parent=ss["Normal"], textColor=INK_SOFT, fontSize=9.5,
                           spaceAfter=14))
    ss.add(ParagraphStyle("LocHeading", parent=ss["Heading2"], textColor=INK, fontSize=14,
                           spaceBefore=18, spaceAfter=6))
    ss.add(ParagraphStyle("Body", parent=ss["Normal"], textColor=INK, fontSize=10, leading=14))
    ss.add(ParagraphStyle("Note", parent=ss["Normal"], textColor=INK_SOFT, fontSize=8.5, leading=12))
    ss.add(ParagraphStyle("Disclaimer", parent=ss["Normal"], textColor=INK_SOFT, fontSize=8,
                           leading=11, spaceBefore=24))
    return ss


def _fmt_money(v):
    # "Rs." not "₹" — reportlab's built-in Helvetica has no glyph for U+20B9 and
    # renders it as a black box; avoiding a custom font embed keeps this simple.
    if v is None:
        return "—"
    return f"Rs. {v:,.0f}"


def _fmt_score(v):
    return "—" if v is None else f"{v:g}/100"


def _fmt_pct(v):
    if v is None:
        return "—"
    sign = "+" if v >= 0 else ""
    return f"{sign}{v}%"


def _metric_table(loc):
    business = loc.get("opportunity")
    rows = [
        ["Economic score", _fmt_score(loc.get("economic_score")), "Risk level", loc["risk"]["level"]],
        ["Est. monthly income", _fmt_money(loc.get("income")), "Est. monthly spend", _fmt_money(loc.get("spend"))],
    ]
    if business:
        rows.append(["Opportunity score", _fmt_score(business["opportunity_score"]),
                     "Suitability", business["suitability"]])
    rows.append(["Classification", loc.get("risk_opportunity", "—"), "", ""])

    t = Table(rows, colWidths=[42 * mm, 38 * mm, 42 * mm, 38 * mm])
    style = [
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTNAME", (2, 0), (2, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9.5),
        ("TEXTCOLOR", (0, 0), (-1, -1), INK),
        ("TEXTCOLOR", (0, 0), (0, -1), INK_SOFT),
        ("TEXTCOLOR", (2, 0), (2, -1), INK_SOFT),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("LINEBELOW", (0, 0), (-1, -2), 0.5, BORDER),
        ("SPAN", (1, -1), (3, -1)),
    ]
    risk_color = RISK_COLOR.get(loc["risk"]["level"], INK_SOFT)
    style.append(("TEXTCOLOR", (3, 0), (3, 0), risk_color))
    style.append(("FONTNAME", (3, 0), (3, 0), "Helvetica-Bold"))
    t.setStyle(TableStyle(style))
    return t


def _benchmark_table(benchmark):
    rows = [["Compared to", "PPI (mean)", "vs. this location"]]
    for key in ("state", "district", "neighbours"):
        b = benchmark.get(key)
        if not b:
            continue
        rows.append([b["label"], b["ppi_ml"] if b["ppi_ml"] is not None else "—", _fmt_pct(b["diff_pct"])])
    if len(rows) == 1:
        return None
    t = Table(rows, colWidths=[70 * mm, 35 * mm, 35 * mm])
    t.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("TEXTCOLOR", (0, 0), (-1, 0), INK_SOFT),
        ("TEXTCOLOR", (0, 1), (-1, -1), INK),
        ("BACKGROUND", (0, 0), (-1, 0), PAPER_2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("LINEBELOW", (0, 0), (-1, -1), 0.5, BORDER),
    ]))
    return t


def build_project_report_pdf(project, locations, out_path):
    """project: a projects table row (dict). locations: list of
    blueprints.intelligence payload dicts (may include 'opportunity').
    Writes the PDF to out_path."""
    styles = _styles()
    doc = SimpleDocTemplate(
        out_path, pagesize=A4,
        topMargin=22 * mm, bottomMargin=18 * mm, leftMargin=20 * mm, rightMargin=20 * mm,
    )
    story = []

    story.append(Paragraph("PaisaMap", styles["Brand"]))
    story.append(Paragraph("Location Intelligence Report", styles["ReportTitle"]))
    meta_bits = [project.get("name", "Untitled project")]
    biz = " · ".join(b for b in (project.get("business_type"), project.get("target_segment")) if b)
    if biz:
        meta_bits.append(biz)
    if project.get("avg_ticket"):
        meta_bits.append(f"avg ticket {_fmt_money(project['avg_ticket'])}")
    meta_bits.append(datetime.now(timezone.utc).strftime("Generated %d %b %Y"))
    story.append(Paragraph(" &nbsp;·&nbsp; ".join(meta_bits), styles["Meta"]))
    story.append(HRFlowable(width="100%", thickness=1, color=BORDER, spaceAfter=10))

    if not locations:
        story.append(Paragraph("This project has no saved locations yet.", styles["Body"]))

    for i, loc in enumerate(locations):
        story.append(Paragraph(f"{loc.get('name', loc['pincode'])} — {loc['pincode']}", styles["LocHeading"]))
        story.append(_metric_table(loc))
        story.append(Spacer(1, 8))
        bench_table = _benchmark_table(loc.get("benchmark") or {})
        if bench_table:
            story.append(bench_table)
            story.append(Spacer(1, 8))
        story.append(Paragraph(loc.get("executive_summary", ""), styles["Body"]))
        story.append(Spacer(1, 4))
        story.append(Paragraph(loc["risk"]["note"], styles["Note"]))
        if i < len(locations) - 1:
            story.append(HRFlowable(width="100%", thickness=0.5, color=BORDER, spaceBefore=14, spaceAfter=4))

    story.append(Paragraph(
        "All figures are modelled estimates from PaisaMap's PPI ensemble, not real transaction "
        "records. Opportunity/suitability scoring is a documented heuristic (purchasing power "
        "plus ticket-size affordability fit), not a fitted prediction of business outcomes. "
        "Risk reflects signal-profile volatility only, not crime, safety, or business-failure risk.",
        styles["Disclaimer"],
    ))

    doc.build(story)
    return out_path
