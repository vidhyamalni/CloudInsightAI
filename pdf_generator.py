"""CloudInsight AI — compact 3-page executive PDF report (ReportLab)."""
from io import BytesIO
from xml.sax.saxutils import escape
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle

NAVY, BLUE, LIGHT, LINE = colors.HexColor("#0F172A"), colors.HexColor("#2563EB"), colors.HexColor("#F1F5FB"), colors.HexColor("#DCE4F0")


def _styles():
    h = ParagraphStyle("h", fontName="Helvetica-Bold", fontSize=11.5, textColor=NAVY, spaceBefore=10, spaceAfter=4)
    b = ParagraphStyle("b", fontName="Helvetica", fontSize=9, leading=12.5, textColor=colors.HexColor("#334155"))
    small = ParagraphStyle("s", fontName="Helvetica", fontSize=8, leading=11, textColor=colors.HexColor("#475569"))
    note = ParagraphStyle("n", fontName="Helvetica-Oblique", fontSize=7.5, leading=10, textColor=colors.HexColor("#64748B"), spaceAfter=3)
    return h, b, small, note


def _tbl(data, widths, header=True, label_cols=False):
    t = Table(data, colWidths=widths)
    style = [("GRID", (0, 0), (-1, -1), 0.4, LINE), ("VALIGN", (0, 0), (-1, -1), "TOP"),
             ("TEXTCOLOR", (0, 0), (-1, -1), colors.HexColor("#334155")), ("FONTSIZE", (0, 0), (-1, -1), 8),
             ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5), ("TOPPADDING", (0, 0), (-1, -1), 3.5),
             ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5)]
    if header:
        style += [("BACKGROUND", (0, 0), (-1, 0), NAVY), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                  ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold")]
    if label_cols:
        c0, c2 = (0, 2) if len(widths) == 4 else (0, 0)
        style += [("BACKGROUND", (c0, 0), (c0, -1), LIGHT), ("FONTNAME", (c0, 0), (c0, -1), "Helvetica-Bold")]
        if len(widths) == 4:
            style += [("BACKGROUND", (2, 0), (2, -1), LIGHT), ("FONTNAME", (2, 0), (2, -1), "Helvetica-Bold")]
    t.setStyle(TableStyle(style))
    return t


def _box(text, style):
    t = Table([[Paragraph(text, style)]], colWidths=[505])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), LIGHT), ("BOX", (0, 0), (-1, -1), 0.8, BLUE),
                           ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                           ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
    return t


def _short(text, n):
    text = str(text).strip()
    return text if len(text) <= n else text[:n - 1].rstrip() + "…"


def _footer(canvas, doc):
    canvas.saveState()
    canvas.setStrokeColor(NAVY); canvas.setLineWidth(0.6)
    canvas.line(16 * mm, 12 * mm, A4[0] - 16 * mm, 12 * mm)
    canvas.setFont("Helvetica", 7); canvas.setFillColor(colors.HexColor("#64748B"))
    canvas.drawString(16 * mm, 8 * mm, "CloudInsight AI — Estimated figures. Verify with provider pricing calculators.")
    canvas.drawRightString(A4[0] - 16 * mm, 8 * mm, f"Page {canvas.getPageNumber()}")
    canvas.restoreState()


def build_pdf(report, ai_insights=None):
    h, b, small, note = _styles()
    d = report
    i, c, s, sec = d["inputs"], d["cost"]["primary"], d["storage"], d["security"]
    suit = d.get("suitability")
    story = []

    # ---------- Header ----------
    story.append(Paragraph("<b>CloudInsight AI</b> — Cloud Analysis Report",
                           ParagraphStyle("t", fontName="Helvetica-Bold", fontSize=15, textColor=NAVY, spaceAfter=2)))
    story.append(Paragraph(f"{escape(i['company_name'])} · Recommended provider: <b>{escape(i['provider'])}</b> · {escape(d['meta']['generated_at'])}", b))
    story.append(Spacer(1, 5))

    # ---------- 1. Executive Summary ----------
    story.append(Paragraph("1. Executive Summary", h))
    for p in d["exec_summary"][:3]:
        story.append(Paragraph("• " + escape(_short(p, 240)), small))

    if suit:
        expl = _short((suit.get("explanation") or "").split(". ")[0], 260)
        story.append(Spacer(1, 4))
        story.append(_box(f"<b>Recommended provider: {escape(suit['winner'])}</b> — "
                          f"{suit['scores'][suit['winner']]['overall']}/100 overall suitability. {escape(expl)}.", b))

    # ---------- 2. Requirements ----------
    story.append(Paragraph("2. Cloud Requirements", h))
    rows = [["Company", escape(i["company_name"]), "Industry", escape(i["industry"])],
            ["Region", escape(i["region"]), "Expected users", str(i["expected_users"])],
            ["Workload", escape(i["compute_req"]), "Data sensitivity", escape(i["data_sensitivity"])],
            ["Availability", escape(i["availability"]), "Compliance", escape(i["compliance"])],
            ["Budget / month", f"${i['monthly_budget']:,.0f}" if i["monthly_budget"] else "—", "Required storage", f"{s['required_gb']} GB"]]
    story.append(_tbl(rows, [85, 168, 85, 167], header=False, label_cols=True))

    # ---------- 3. Suitability (all six providers) ----------
    if suit:
        story.append(Paragraph("3. Provider Suitability — all six providers evaluated", h))
        rows = [["Provider", "Overall /100", "Est. cost /mo", "Verdict"]]
        for r in suit["ranking"]:
            name = r["provider"]
            rows.append([escape(name), f"{r['overall']}", f"${d['cost']['by_provider'][name]:,.2f}", escape(_short(r['verdict'], 42))])
        story.append(_tbl(rows, [130, 62, 78, 235]))

    # ---------- 4. Cost Analysis ----------
    pr = d.get("pricing")                       # absent in reports saved before pricing v2
    status = (pr or {}).get("overall_status") or d.get("meta", {}).get("pricing_status") or "estimated"
    story.append(Paragraph(f"4. Cost Analysis — {escape(i['provider'])} ({escape(str(c.get('pricing_status', status)))} pricing)", h))
    rows = [["Estimated monthly cost", f"${c['monthly']:,.2f}"],
            ["Estimated annual cost (monthly × 12)", f"${c['annual']:,.2f}"],
            ["Storage component", f"${c['storage']:,.2f} /mo"],
            ["Compute component", f"${c['compute']:,.2f} /mo"],
            ["Network egress (est.)", f"${c['network']:,.2f} /mo"]]
    if d["cost"].get("budget_utilization"):
        rows.append(["Budget utilization", f"{d['cost']['budget_utilization']}% of ${i['monthly_budget']:,.0f}/mo"])
    story.append(_tbl(rows, [180, 140], header=False, label_cols=True))
    story.append(Spacer(1, 4))
    rows = [["Provider", "Storage", "Compute", "Network", "Total /mo", "Pricing"]]
    for r in d["cost"]["comparison"]:
        rows.append([escape(r["provider"]), f"${r['storage']:,.2f}", f"${r['compute']:,.2f}",
                     f"${r.get('network', 0):,.2f}", f"${r['monthly']:,.2f}", escape(str(r.get("pricing_status", "estimated")))])
    story.append(_tbl(rows, [120, 72, 72, 72, 80, 89]))
    if pr:
        story.append(Spacer(1, 4))
        prim = pr["providers"].get(i["provider"], {})
        rows = [["Price point", "Rate", "Status", "Source", "As of"]]
        for comp, label in (("storage", "Storage"), ("compute", "Compute"), ("egress", "Egress")):
            q = prim.get(comp)
            if q:
                rows.append([label, f"${q['value']:.4g} {q['unit'].replace('USD ', '')}", escape(q["status"]),
                             escape(_short(q["source"], 38)), escape(q["as_of"] or "not verified")])
        story.append(_tbl(rows, [62, 120, 56, 170, 97]))
        story.append(Paragraph(escape(_short(
            f"Pricing generated {pr['generated_at']} · currency {pr['currency']} · region requested {pr['region_requested']}. "
            f"Price points: {pr['counts']['live']} live, {pr['counts']['cached']} cached, {pr['counts']['estimated']} estimated "
            "(live data is available for Azure only). Egress assumes "
            f"{d['cost'].get('primary', {}).get('egress_gb', 0):,.0f} GB/month; no free-tier allowance applied.", 400)), note))

    # ---------- 5. Storage Analysis ----------
    story.append(Paragraph(f"5. Storage Analysis ({escape(str(s['primary'].get('pricing_status', 'estimated')))} pricing)", h))
    sp = s["primary"]
    rows = [["Storage service", escape(sp["service"])],
            ["Required storage", f"{s['required_gb']} GB (×{s['redundancy_multiplier']} retained copies for {escape(i['data_sensitivity'])} data)"],
            ["Cost per GB / month", f"${sp['per_gb']:.4f}"],
            ["Estimated monthly / annual", f"${sp['monthly']:,.2f}  ·  ${sp['annual']:,.2f}"],
            ["Encryption", escape(_short(s['requirements']['encryption'], 85))],
            ["Backup", escape(_short(s['requirements']['backup'], 85))],
            ["Data retention", escape(s['requirements']['retention'])]]
    story.append(_tbl(rows, [130, 375], header=False, label_cols=True))

    # ---------- 6. Security & Risk ----------
    story.append(Paragraph("6. Security & Risk Summary (simulated configuration analysis)", h))
    story.append(Paragraph(f"Security score <b>{sec['score']}/100 — {escape(sec['risk_level'])} risk</b> · "
                           f"{sec['high']} high, {sec['medium']} medium, {sec['low']} low findings across 13 domains.", b))
    rows = [["Priority", "Domain", "Key action"]]
    for x in [d2 for d2 in sec["domains"] if d2["status"] == "gap"][:4]:
        rows.append(["HIGH", escape(x["domain"]), escape(_short(x["note"], 95))])
    for x in [d2 for d2 in sec["domains"] if d2["status"] == "review"][:2]:
        rows.append(["Medium", escape(x["domain"]), escape(_short(x["note"], 95))])
    if len(rows) > 1:
        story.append(_tbl(rows, [48, 130, 327]))

    # ---------- 7. Recommendations ----------
    story.append(Paragraph("7. Top Recommendations", h))
    for n, r in enumerate(d["recommendations"][:5], 1):
        story.append(Paragraph(f"{n}. {escape(_short(r, 170))}", small))

    # ---------- 8. Assumptions & disclaimer ----------
    story.append(Paragraph("8. Key Assumptions", h))
    for a in d["assumptions"][:4]:
        story.append(Paragraph("• " + escape(_short(a, 200)), note))
    story.append(Spacer(1, 6))
    story.append(Paragraph("Disclaimer: cost figures are estimates, not billing amounts; each price point is labelled live, cached or estimated "
                           "in section 4. Security figures are simulated from configured baselines, not a live scan. "
                           "Verify with provider pricing calculators.", note))
    if ai_insights and ai_insights.get("summary"):
        story.append(Paragraph("<b>AI summary (Gemini):</b> " + escape(_short(ai_insights["summary"], 300)), note))

    buf = BytesIO()
    SimpleDocTemplate(buf, pagesize=A4, leftMargin=16 * mm, rightMargin=16 * mm, topMargin=13 * mm,
                      bottomMargin=17 * mm, title="CloudInsight AI — Cloud Analysis Report").build(
        story, onFirstPage=_footer, onLaterPages=_footer)
    return buf.getvalue()