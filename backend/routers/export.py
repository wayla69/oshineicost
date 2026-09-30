"""The monthly review pack: PDF and workbook export.

Moved out of main.py in Phase 2b (2026-09) by the AST: these endpoints and
the helpers only they use, unchanged. URLs are unchanged.
"""
from fastapi import APIRouter
from core import _cogs_case
from core import _costed_rev_case
from core import _ym_parts
from core import get_db
from fastapi import HTTPException
from fastapi import Query
from fastapi.responses import StreamingResponse
import io as _io

router = APIRouter()


# ---------------------------------------------------------------------------
# GET /api/export/monthly-pack   (Deliverable B — Monthly Review Pack)
# ---------------------------------------------------------------------------
def _mpack_collect(month: str) -> dict:
    """Gather all the numbers needed for the Monthly Review Pack.
    Honours Rule 1 (ex-VAT revenue from sales_sku_monthly)."""
    from datetime import date as _date_cls, timedelta as _td
    # Prior month
    y, mo = _ym_parts(month)
    first = _date_cls(y, mo, 1)
    prior_first = (first - _td(days=1)).replace(day=1)
    prior_month = prior_first.strftime("%Y-%m")

    cogs_expr       = _cogs_case('ssm', 'sk', 'sc')
    costed_rev_expr = _costed_rev_case('ssm', 'sk', 'sc')

    with get_db() as con:
        # Targets from app_settings
        try:
            srows = con.execute(
                "SELECT key, value FROM app_settings WHERE key IN ('cost_target_lo','cost_target_hi')"
            ).fetchall()
            settings = {r["key"]: r["value"] for r in srows}
        except Exception:
            settings = {}
        try:
            target_lo = float(settings.get("cost_target_lo", 55))
        except Exception:
            target_lo = 55.0
        try:
            target_hi = float(settings.get("cost_target_hi", 70))
        except Exception:
            target_hi = 70.0

        # Network summary — current + prior
        def _net(m: str) -> dict:
            r = con.execute(f"""
                SELECT COALESCE(SUM(ssm.subtotal_ex_vat),0)  AS revenue,
                       COALESCE(SUM({cogs_expr}),0)          AS cogs,
                       COALESCE(SUM({costed_rev_expr}),0)    AS costed_rev,
                       COUNT(DISTINCT ssm.branch_id)         AS branch_count
                  FROM v_sales_sku_monthly ssm
                  JOIN branches b ON b.branch_id = ssm.branch_id
                  JOIN skus     sk ON sk.sku_id  = ssm.sku_id
                  LEFT JOIN sku_cost sc ON sc.sku_id = ssm.sku_id
                 WHERE ssm.period_month = :m
                   AND b.name != 'ALL'
                   AND COALESCE(b.active,1) = 1
            """, {"m": m}).fetchone()
            return {
                "month":       m,
                "revenue":     float(r["revenue"]    or 0),
                "cogs":        float(r["cogs"]       or 0),
                "costed_rev":  float(r["costed_rev"] or 0),
                "branch_count": int(r["branch_count"] or 0),
            }
        summary_cur   = _net(month)
        summary_prior = _net(prior_month)

        # P&L per branch (current month)
        branch_rows = con.execute(f"""
            SELECT b.name AS branch,
                   COALESCE(SUM(ssm.subtotal_ex_vat),0) AS revenue,
                   COALESCE(SUM({cogs_expr}),0)         AS cogs,
                   COALESCE(SUM({costed_rev_expr}),0)   AS costed_rev
              FROM v_sales_sku_monthly ssm
              JOIN branches b ON b.branch_id = ssm.branch_id
              JOIN skus     sk ON sk.sku_id  = ssm.sku_id
              LEFT JOIN sku_cost sc ON sc.sku_id = ssm.sku_id
             WHERE ssm.period_month = :m
               AND b.name != 'ALL'
               AND COALESCE(b.active,1) = 1
             GROUP BY b.branch_id
             ORDER BY revenue DESC
        """, {"m": month}).fetchall()

        # Top 30 cost drivers (current month, network-wide)
        top_drivers = con.execute(f"""
            SELECT sk.sku_code,
                   COALESCE(sk.description, sk.sku_code) AS description,
                   SUM(ssm.qty)                          AS qty,
                   SUM({cogs_expr}) / NULLIF(SUM(ssm.qty),0) AS unit_cost,
                   SUM({cogs_expr})                      AS total_cogs
              FROM v_sales_sku_monthly ssm
              JOIN branches b ON b.branch_id = ssm.branch_id
              JOIN skus     sk ON sk.sku_id  = ssm.sku_id
              LEFT JOIN sku_cost sc ON sc.sku_id = ssm.sku_id
             WHERE ssm.period_month = :m
               AND b.name != 'ALL'
               AND COALESCE(b.active,1) = 1
             GROUP BY sk.sku_id
             HAVING total_cogs > 0
             ORDER BY total_cogs DESC
             LIMIT 50
        """, {"m": month}).fetchall()

        # For sparklines: last-12-month qty per SKU (top drivers only)
        spark_data: dict = {}
        if top_drivers:
            # Build SKU id list
            sku_ids = con.execute(f"""
                SELECT sku_id, sku_code FROM skus
                 WHERE sku_code IN ({",".join(":c"+str(i) for i in range(len(top_drivers)))})
            """, {f"c{i}": r["sku_code"] for i, r in enumerate(top_drivers)}).fetchall()
            code_to_id = {r["sku_code"]: r["sku_id"] for r in sku_ids}
            # 12-month window ending at `month`
            y2, m2 = _ym_parts(month)
            from_dt = _date_cls(y2, m2, 1)
            for _ in range(11):
                from_dt = (from_dt.replace(day=1) - _td(days=1)).replace(day=1)
            month_from_12 = from_dt.strftime("%Y-%m")
            ids = list(code_to_id.values())
            if ids:
                ph = ",".join(":s"+str(i) for i in range(len(ids)))
                qrows = con.execute(f"""
                    SELECT sku_id, period_month, SUM(qty) AS qty
                      FROM v_sales_sku_monthly
                     WHERE sku_id IN ({ph})
                       AND period_month >= :mf AND period_month <= :mt
                     GROUP BY sku_id, period_month
                     ORDER BY sku_id, period_month
                """, {**{f"s{i}": sid for i, sid in enumerate(ids)},
                      "mf": month_from_12, "mt": month}).fetchall()
                for qr in qrows:
                    spark_data.setdefault(qr["sku_id"], []).append(
                        (qr["period_month"], float(qr["qty"] or 0))
                    )
            # Map back by sku_code
            top_drivers_out = []
            total_cost = sum(float(r["total_cogs"] or 0) for r in top_drivers) or 1.0
            for r in top_drivers:
                sid = code_to_id.get(r["sku_code"])
                top_drivers_out.append({
                    "sku_code":    r["sku_code"],
                    "description": r["description"],
                    "qty":         float(r["qty"] or 0),
                    "unit_cost":   float(r["unit_cost"] or 0),
                    "total_cogs":  float(r["total_cogs"] or 0),
                    "pct_of_total": float(r["total_cogs"] or 0) / total_cost * 100,
                    "sparkline":   spark_data.get(sid, []),
                })
        else:
            top_drivers_out = []

        # Top 25 ingredients across all branches (current month)
        ing_rows = con.execute("""
            WITH eff AS (
              SELECT ssm.sku_id, ssm.qty,
                COALESCE(
                  (SELECT bi.sku_id FROM bom_ingredients bi WHERE bi.sku_id = ssm.sku_id LIMIT 1),
                  (SELECT a.resolved_sku_id FROM bom_sku_alias a
                     JOIN skus s ON s.sku_id = ssm.sku_id
                     WHERE a.bom_sku_code = s.sku_code LIMIT 1)
                ) AS bom_sku_id
              FROM v_sales_sku_monthly ssm
              JOIN branches b ON b.branch_id = ssm.branch_id
              WHERE ssm.period_month = :m
                AND b.name != 'ALL'
                AND COALESCE(b.active,1) = 1
            )
            SELECT COALESCE(NULLIF(bi.ingredient_code,''), '__'||bi.ingredient_name) AS ing_key,
                   MAX(bi.ingredient_code) AS code,
                   MAX(bi.ingredient_name) AS name,
                   MAX(bi.ingredient_type) AS type,
                   MAX(bi.unit) AS unit,
                   SUM(eff.qty * bi.qty_per_serving) AS total_qty,
                   SUM(eff.qty * bi.ingredient_cost) AS total_cost
              FROM eff
              JOIN bom_ingredients bi ON bi.sku_id = eff.bom_sku_id
             WHERE eff.bom_sku_id IS NOT NULL
             GROUP BY ing_key
             ORDER BY total_cost DESC
             LIMIT 50
        """, {"m": month}).fetchall()

    # Derive
    def _enrich(s):
        rev = s["revenue"]; cogs = s["cogs"]
        s["gp"]            = rev - cogs
        s["gp_pct"]        = (s["gp"]/rev*100) if rev > 0 else None
        s["food_cost_pct"] = (cogs/rev*100)    if rev > 0 else None
        return s
    summary_cur   = _enrich(summary_cur)
    summary_prior = _enrich(summary_prior)

    def _delta_pct(cur, prev):
        if prev is None or prev == 0: return None
        return (cur - prev) / prev * 100
    summary_cur["vs_prior_revenue_pct"] = _delta_pct(summary_cur["revenue"], summary_prior["revenue"])
    summary_cur["vs_prior_gp_pct_pp"]   = (
        (summary_cur["gp_pct"] - summary_prior["gp_pct"])
        if summary_cur["gp_pct"] is not None and summary_prior["gp_pct"] is not None else None
    )

    # Per-branch P&L
    branches = []
    alerts = []
    for r in branch_rows:
        rev = float(r["revenue"] or 0)
        cogs = float(r["cogs"] or 0)
        cr   = float(r["costed_rev"] or 0)
        gp   = rev - cogs
        gp_p = (gp/rev*100)   if rev > 0 else None
        fc_p = (cogs/rev*100) if rev > 0 else None
        if fc_p is None:
            status = "no-data"
        elif fc_p > target_hi:
            status = "out"
        elif fc_p < target_lo - 5:
            status = "low"        # suspiciously low
        elif fc_p > target_hi - 3:
            status = "borderline"
        else:
            status = "in"
        branches.append({
            "branch": r["branch"], "revenue": rev, "cogs": cogs,
            "costed_rev": cr, "gp": gp, "gp_pct": gp_p, "food_cost_pct": fc_p,
            "status": status,
        })
        # Alerts
        if fc_p is not None and fc_p > 70.0:
            alerts.append({
                "branch": r["branch"], "severity": "high",
                "message": f"Food cost {fc_p:.1f}% เกิน 70% (เป้า {target_lo}-{target_hi}%)",
            })
        if gp is not None and gp < 0:
            alerts.append({
                "branch": r["branch"], "severity": "critical",
                "message": f"GP ติดลบ {gp:,.0f}฿ — ทบทวนต้นทุนเร่งด่วน",
            })

    # Top ingredients
    total_ing_cost = sum(float(r["total_cost"] or 0) for r in ing_rows) or 1.0
    ingredients = []
    for r in ing_rows:
        c = float(r["total_cost"] or 0)
        ingredients.append({
            "ingredient_code": r["code"] or "",
            "ingredient_name": r["name"] or "",
            "ingredient_type": r["type"] or "other",
            "unit":            r["unit"] or "",
            "total_qty":       float(r["total_qty"] or 0),
            "total_cost":      c,
            "pct_of_total":    c / total_ing_cost * 100,
        })

    # Auto action items
    actions = []
    # 1) Branches over target
    over = sorted([b for b in branches if b["food_cost_pct"] and b["food_cost_pct"] > target_hi],
                  key=lambda x: -x["food_cost_pct"])[:5]
    for b in over:
        actions.append(f"ทบทวน BoM/ราคาขาย ที่สาขา {b['branch']} — Food Cost {b['food_cost_pct']:.1f}% (เป้า ≤{target_hi}%)")
    # 2) Top cost driver concentration
    if top_drivers_out:
        top5_pct = sum(d["pct_of_total"] for d in top_drivers_out[:5])
        if top5_pct >= 30:
            actions.append(f"Top 5 SKU คิดเป็น {top5_pct:.1f}% ของต้นทุน — เจรจาราคากับ supplier")
    # 3) Top ingredients
    if ingredients:
        top3 = ingredients[:3]
        names = ", ".join(i["ingredient_name"] for i in top3)
        actions.append(f"วัตถุดิบต้นทุนสูงสุด: {names} — เปรียบเทียบราคา supplier รายเดือน")
    # 4) GP trend
    if summary_cur.get("vs_prior_gp_pct_pp") is not None and summary_cur["vs_prior_gp_pct_pp"] < -2:
        actions.append(f"GP% ลดลง {abs(summary_cur['vs_prior_gp_pct_pp']):.1f} pp จากเดือนก่อน — สอบสวนสาเหตุ")
    if not actions:
        actions.append("ไม่มีรายการที่ต้องดำเนินการเร่งด่วนในเดือนนี้")

    return {
        "month": month,
        "prior_month": prior_month,
        "summary_current": summary_cur,
        "summary_prior":   summary_prior,
        "target_lo": target_lo,
        "target_hi": target_hi,
        "branches":  branches,
        "top_drivers": top_drivers_out,
        "ingredients": ingredients,
        "alerts": alerts,
        "actions": actions,
    }


def _mpack_render_xlsx(pack: dict) -> bytes:
    """Render the Monthly Pack to an xlsx workbook."""
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.utils import get_column_letter

    HDR_FONT  = Font(bold=True, color="FFFFFF", size=11)
    HDR_FILL  = PatternFill("solid", fgColor="1F4E79")
    TITLE     = Font(bold=True, size=14, color="1F4E79")
    SUBTITLE  = Font(italic=True, size=10, color="64748B")
    CENTER    = Alignment(horizontal="center", vertical="center")
    NUM_FMT   = '#,##0'
    PCT_FMT   = '0.0"%"'

    STATUS_COLOR = {
        "in":         ("E8F5E9", "1B5E20", "ในเป้า"),
        "borderline": ("FFF3E0", "E65100", "⚠ ใกล้ขอบ"),
        "out":        ("FFEBEE", "B71C1C", "เกินเป้า"),
        "low":        ("E3F2FD", "0D47A1", "ℹ️ ต่ำผิดปกติ"),
        "no-data":    ("ECEFF1", "455A64", "— ไม่มีข้อมูล"),
    }

    def make_header(ws, headers, row=1):
        for i, h in enumerate(headers, start=1):
            c = ws.cell(row=row, column=i, value=h)
            c.font = HDR_FONT; c.fill = HDR_FILL; c.alignment = CENTER

    def autofit(ws, min_w=10, max_w=42):
        for col in ws.columns:
            mx = max((len(str(cell.value)) if cell.value is not None else 0) for cell in col)
            ws.column_dimensions[get_column_letter(col[0].column)].width = max(min_w, min(mx + 2, max_w))

    wb = openpyxl.Workbook()

    # ── Sheet: Summary ────────────────────────────────────────────────────
    ws = wb.active
    ws.title = "Summary"
    sc = pack["summary_current"]; sp = pack["summary_prior"]
    ws["A1"] = f"Oshinei — Monthly Review Pack · {pack['month']}"
    ws["A1"].font = TITLE
    ws.merge_cells("A1:E1")
    ws["A2"] = f"เทียบกับ {pack['prior_month']} · เป้า Food Cost {pack['target_lo']:.0f}–{pack['target_hi']:.0f}%"
    ws["A2"].font = SUBTITLE
    ws.merge_cells("A2:E2")
    make_header(ws, ["Metric", pack["month"], pack["prior_month"], "Δ", "Δ %"], row=4)
    def _row(label, cur, prev, fmt=NUM_FMT, is_pct=False):
        r = ws.max_row + 1
        ws.cell(row=r, column=1, value=label).font = Font(bold=True)
        ws.cell(row=r, column=2, value=cur).number_format  = fmt
        ws.cell(row=r, column=3, value=prev).number_format = fmt
        if cur is not None and prev is not None:
            delta = cur - prev
            ws.cell(row=r, column=4, value=delta).number_format = fmt
            if prev != 0 and not is_pct:
                ws.cell(row=r, column=5, value=(delta/prev*100)).number_format = PCT_FMT
    _row("Revenue (ex-VAT)",   sc["revenue"],       sp["revenue"])
    _row("COGS",               sc["cogs"],          sp["cogs"])
    _row("Gross Profit",       sc["gp"],            sp["gp"])
    _row("GP %",               sc["gp_pct"],        sp["gp_pct"], fmt=PCT_FMT, is_pct=True)
    _row("Food Cost %",        sc["food_cost_pct"], sp["food_cost_pct"], fmt=PCT_FMT, is_pct=True)
    _row("Branch count",       sc["branch_count"],  sp["branch_count"])
    autofit(ws)

    # ── Sheet: P&L per Branch ─────────────────────────────────────────────
    ws2 = wb.create_sheet("P&L per Branch")
    make_header(ws2, ["Branch", "Revenue", "COGS", "GP", "GP %", "Food Cost %", "Status"])
    for b in pack["branches"]:
        r = ws2.max_row + 1
        ws2.cell(row=r, column=1, value=b["branch"])
        ws2.cell(row=r, column=2, value=b["revenue"]).number_format       = NUM_FMT
        ws2.cell(row=r, column=3, value=b["cogs"]).number_format          = NUM_FMT
        ws2.cell(row=r, column=4, value=b["gp"]).number_format            = NUM_FMT
        ws2.cell(row=r, column=5, value=b["gp_pct"]).number_format        = PCT_FMT
        ws2.cell(row=r, column=6, value=b["food_cost_pct"]).number_format = PCT_FMT
        bg, fg, label = STATUS_COLOR.get(b["status"], STATUS_COLOR["no-data"])
        sc_cell = ws2.cell(row=r, column=7, value=label)
        sc_cell.fill = PatternFill("solid", fgColor=bg)
        sc_cell.font = Font(color=fg, bold=True)
    autofit(ws2)

    # ── Sheet: Top 50 Cost Drivers ────────────────────────────────────────
    ws3 = wb.create_sheet("Top 50 Cost Drivers")
    make_header(ws3, ["SKU", "Description", "Qty", "Unit Cost", "Total COGS",
                       "% of Total", "12-mo Qty Trend"])
    for d in pack["top_drivers"]:
        r = ws3.max_row + 1
        ws3.cell(row=r, column=1, value=d["sku_code"])
        ws3.cell(row=r, column=2, value=d["description"])
        ws3.cell(row=r, column=3, value=d["qty"]).number_format        = NUM_FMT
        ws3.cell(row=r, column=4, value=d["unit_cost"]).number_format  = '#,##0.00'
        ws3.cell(row=r, column=5, value=d["total_cogs"]).number_format = NUM_FMT
        ws3.cell(row=r, column=6, value=d["pct_of_total"]).number_format = PCT_FMT
        spark = ",".join(f"{m}:{int(q)}" for m, q in d.get("sparkline", []))
        ws3.cell(row=r, column=7, value=spark)
    autofit(ws3)

    # ── Sheet: BoM Ingredients ────────────────────────────────────────────
    ws4 = wb.create_sheet("BoM Ingredients")
    make_header(ws4, ["Code", "Name", "Type", "Unit", "Total Qty",
                       "Total Cost", "% of Total"])
    for ing in pack["ingredients"]:
        r = ws4.max_row + 1
        ws4.cell(row=r, column=1, value=ing["ingredient_code"])
        ws4.cell(row=r, column=2, value=ing["ingredient_name"])
        ws4.cell(row=r, column=3, value=ing["ingredient_type"])
        ws4.cell(row=r, column=4, value=ing["unit"])
        ws4.cell(row=r, column=5, value=ing["total_qty"]).number_format    = '#,##0.00'
        ws4.cell(row=r, column=6, value=ing["total_cost"]).number_format   = NUM_FMT
        ws4.cell(row=r, column=7, value=ing["pct_of_total"]).number_format = PCT_FMT
    autofit(ws4)

    # ── Sheet: Alerts ─────────────────────────────────────────────────────
    ws5 = wb.create_sheet("Alerts")
    make_header(ws5, ["Branch", "Severity", "Message"])
    if not pack["alerts"]:
        ws5.cell(row=2, column=1, value="ไม่มี alert ในเดือนนี้").font = Font(italic=True)
    for a in pack["alerts"]:
        r = ws5.max_row + 1
        ws5.cell(row=r, column=1, value=a["branch"])
        sev_cell = ws5.cell(row=r, column=2, value=a["severity"])
        if a["severity"] == "critical":
            sev_cell.fill = PatternFill("solid", fgColor="FFEBEE")
            sev_cell.font = Font(color="B71C1C", bold=True)
        elif a["severity"] == "high":
            sev_cell.fill = PatternFill("solid", fgColor="FFF3E0")
            sev_cell.font = Font(color="E65100", bold=True)
        ws5.cell(row=r, column=3, value=a["message"])
    autofit(ws5)

    # ── Sheet: Action Items ───────────────────────────────────────────────
    ws6 = wb.create_sheet("Action Items")
    make_header(ws6, ["#", "Action"])
    for i, a in enumerate(pack["actions"], start=1):
        ws6.cell(row=i + 1, column=1, value=i)
        ws6.cell(row=i + 1, column=2, value=a)
    autofit(ws6)

    buf = _io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _mpack_render_pdf(pack: dict) -> bytes:
    """Render the Monthly Pack to a multi-page PDF using reportlab + matplotlib."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib import colors
    from reportlab.lib.units import cm
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
        PageBreak, Image as RLImage,
    )
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    buf = _io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4,
                            leftMargin=1.5*cm, rightMargin=1.5*cm,
                            topMargin=1.5*cm, bottomMargin=1.5*cm,
                            title=f"Monthly Pack {pack['month']}")
    styles = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=styles["Title"], textColor=colors.HexColor("#1F4E79"))
    h2 = ParagraphStyle("h2", parent=styles["Heading2"], textColor=colors.HexColor("#1F4E79"))
    body  = styles["BodyText"]
    note  = ParagraphStyle("note", parent=body, textColor=colors.grey, fontSize=9, italic=True)
    story = []

    sc = pack["summary_current"]; sp = pack["summary_prior"]
    fmt_n = lambda v: f"{v:,.0f}" if isinstance(v, (int,float)) else "—"
    fmt_p = lambda v: f"{v:,.1f}%" if isinstance(v, (int,float)) else "—"

    # ── Cover page ────────────────────────────────────────────────────────
    story.append(Spacer(1, 3*cm))
    story.append(Paragraph("Oshinei Monthly Review Pack", h1))
    story.append(Paragraph(f"{pack['month']}", h2))
    story.append(Spacer(1, 1*cm))
    story.append(Paragraph(f"<b>{sc['branch_count']}</b> สาขา · เป้า Food Cost <b>{pack['target_lo']:.0f}–{pack['target_hi']:.0f}%</b>", body))
    story.append(Spacer(1, 0.6*cm))
    cover_tbl = Table([
        ["Metric", pack["month"], pack["prior_month"]],
        ["Revenue (ex-VAT)", fmt_n(sc["revenue"]),      fmt_n(sp["revenue"])],
        ["COGS",             fmt_n(sc["cogs"]),         fmt_n(sp["cogs"])],
        ["Gross Profit",     fmt_n(sc["gp"]),           fmt_n(sp["gp"])],
        ["GP %",             fmt_p(sc["gp_pct"]),       fmt_p(sp["gp_pct"])],
        ["Food Cost %",      fmt_p(sc["food_cost_pct"]),fmt_p(sp["food_cost_pct"])],
    ], colWidths=[5*cm, 5*cm, 5*cm])
    cover_tbl.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#1F4E79")),
        ("TEXTCOLOR",  (0,0), (-1,0), colors.white),
        ("FONTNAME",   (0,0), (-1,0), "Helvetica-Bold"),
        ("ALIGN",      (1,1), (-1,-1), "RIGHT"),
        ("FONTNAME",   (0,1), (0,-1), "Helvetica-Bold"),
        ("GRID",       (0,0), (-1,-1), 0.5, colors.HexColor("#cbd5e1")),
        ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.whitesmoke, colors.white]),
    ]))
    story.append(cover_tbl)
    story.append(Spacer(1, 1.5*cm))
    story.append(Paragraph("Generated by Oshinei Dashboard · Rule: revenue is ex-VAT.", note))
    story.append(PageBreak())

    # ── P&L per Branch ────────────────────────────────────────────────────
    story.append(Paragraph("P&L per Branch", h2))
    # Bar chart of GP% per branch
    branches = pack["branches"]
    if branches:
        labels = [b["branch"] for b in branches]
        gpps   = [b["gp_pct"] if b["gp_pct"] is not None else 0 for b in branches]
        fig, ax = plt.subplots(figsize=(7.5, max(3, len(labels)*0.25)))
        bar_colors = []
        for b in branches:
            st = b["status"]
            bar_colors.append({"in":"#10b981","borderline":"#f59e0b","out":"#ef4444",
                               "low":"#3b82f6","no-data":"#94a3b8"}.get(st, "#94a3b8"))
        ax.barh(labels, gpps, color=bar_colors)
        ax.invert_yaxis()
        ax.set_xlabel("GP %")
        ax.axvline(100 - pack["target_hi"], color="#10b981", linestyle="--", lw=0.7, alpha=0.5)
        ax.axvline(100 - pack["target_lo"], color="#10b981", linestyle="--", lw=0.7, alpha=0.5)
        ax.grid(axis="x", alpha=0.2)
        fig.tight_layout()
        imgbuf = _io.BytesIO()
        fig.savefig(imgbuf, format="png", dpi=110)
        plt.close(fig)
        imgbuf.seek(0)
        story.append(RLImage(imgbuf, width=17*cm, height=min(18*cm, max(5*cm, len(labels)*0.6*cm))))
        story.append(Spacer(1, 0.4*cm))

    # Branch table
    tbl_data = [["Branch", "Revenue", "COGS", "GP", "GP %", "FC %", "Status"]]
    for b in branches:
        tbl_data.append([
            b["branch"], fmt_n(b["revenue"]), fmt_n(b["cogs"]), fmt_n(b["gp"]),
            fmt_p(b["gp_pct"]), fmt_p(b["food_cost_pct"]),
            {"in":"✓","borderline":"⚠","out":"●","low":"ℹ️","no-data":"—"}.get(b["status"],"—"),
        ])
    branch_tbl = Table(tbl_data, colWidths=[3.5*cm, 2.5*cm, 2.5*cm, 2.5*cm, 1.8*cm, 1.8*cm, 1.4*cm])
    branch_tbl.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#1F4E79")),
        ("TEXTCOLOR",  (0,0), (-1,0), colors.white),
        ("FONTNAME",   (0,0), (-1,0), "Helvetica-Bold"),
        ("FONTSIZE",   (0,0), (-1,-1), 8),
        ("ALIGN",      (1,1), (-1,-1), "RIGHT"),
        ("ALIGN",      (6,1), (6,-1), "CENTER"),
        ("GRID",       (0,0), (-1,-1), 0.3, colors.HexColor("#cbd5e1")),
        ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.whitesmoke, colors.white]),
    ]))
    story.append(branch_tbl)
    story.append(PageBreak())

    # ── Top 50 Cost Drivers ───────────────────────────────────────────────
    story.append(Paragraph("Top 50 Cost Drivers", h2))
    drivers = pack["top_drivers"]
    if drivers:
        # Chart: top 25 cost share (larger since list is now 50)
        top25 = drivers[:25]
        fig, ax = plt.subplots(figsize=(7.5, 7))
        ax.barh([d["sku_code"] for d in top25][::-1],
                [d["total_cogs"] for d in top25][::-1], color="#d946ef")
        ax.set_xlabel("Total COGS (฿)")
        ax.grid(axis="x", alpha=0.2)
        fig.tight_layout()
        imgbuf = _io.BytesIO()
        fig.savefig(imgbuf, format="png", dpi=110)
        plt.close(fig)
        imgbuf.seek(0)
        story.append(RLImage(imgbuf, width=17*cm, height=10*cm))
        story.append(Spacer(1, 0.3*cm))

        tbl_data = [["SKU", "Description", "Qty", "Unit Cost", "COGS", "%"]]
        for d in drivers[:30]:
            tbl_data.append([
                d["sku_code"], (d["description"] or "")[:32],
                fmt_n(d["qty"]), f"{d['unit_cost']:,.2f}",
                fmt_n(d["total_cogs"]), f"{d['pct_of_total']:.1f}%",
            ])
        t = Table(tbl_data, colWidths=[2.3*cm, 7*cm, 2*cm, 2*cm, 2.3*cm, 1.4*cm])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#1F4E79")),
            ("TEXTCOLOR",  (0,0), (-1,0), colors.white),
            ("FONTNAME",   (0,0), (-1,0), "Helvetica-Bold"),
            ("FONTSIZE",   (0,0), (-1,-1), 7),
            ("ALIGN",      (2,1), (-1,-1), "RIGHT"),
            ("GRID",       (0,0), (-1,-1), 0.3, colors.HexColor("#cbd5e1")),
        ]))
        story.append(t)
    story.append(PageBreak())

    # ── BoM Ingredients ───────────────────────────────────────────────────
    story.append(Paragraph("Top 25 Ingredients", h2))
    ings = pack["ingredients"]
    if ings:
        fig, ax = plt.subplots(figsize=(7.5, 5))
        ax.barh([i["ingredient_name"][:24] for i in ings][::-1],
                [i["total_cost"] for i in ings][::-1], color="#0891b2")
        ax.set_xlabel("Total Cost (฿)")
        ax.grid(axis="x", alpha=0.2)
        fig.tight_layout()
        imgbuf = _io.BytesIO()
        fig.savefig(imgbuf, format="png", dpi=110)
        plt.close(fig)
        imgbuf.seek(0)
        story.append(RLImage(imgbuf, width=17*cm, height=11*cm))
        story.append(Spacer(1, 0.3*cm))

        tbl_data = [["Code", "Name", "Type", "Qty", "Cost", "%"]]
        for i in ings:
            tbl_data.append([
                i["ingredient_code"], i["ingredient_name"][:28],
                i["ingredient_type"], f"{i['total_qty']:,.1f}",
                fmt_n(i["total_cost"]), f"{i['pct_of_total']:.1f}%",
            ])
        t = Table(tbl_data, colWidths=[2.2*cm, 6.5*cm, 2.2*cm, 2*cm, 2.3*cm, 1.4*cm])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#1F4E79")),
            ("TEXTCOLOR",  (0,0), (-1,0), colors.white),
            ("FONTNAME",   (0,0), (-1,0), "Helvetica-Bold"),
            ("FONTSIZE",   (0,0), (-1,-1), 7),
            ("ALIGN",      (3,1), (-1,-1), "RIGHT"),
            ("GRID",       (0,0), (-1,-1), 0.3, colors.HexColor("#cbd5e1")),
        ]))
        story.append(t)
    story.append(PageBreak())

    # ── Alerts ────────────────────────────────────────────────────────────
    story.append(Paragraph("Alerts", h2))
    alerts = pack["alerts"]
    if not alerts:
        story.append(Paragraph("ไม่มี alert ในเดือนนี้", note))
    else:
        a_data = [["Branch", "Severity", "Message"]]
        for a in alerts:
            a_data.append([a["branch"], a["severity"], a["message"]])
        a_tbl = Table(a_data, colWidths=[3.5*cm, 2.5*cm, 11*cm])
        a_tbl.setStyle(TableStyle([
            ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#1F4E79")),
            ("TEXTCOLOR",  (0,0), (-1,0), colors.white),
            ("FONTNAME",   (0,0), (-1,0), "Helvetica-Bold"),
            ("FONTSIZE",   (0,0), (-1,-1), 9),
            ("GRID",       (0,0), (-1,-1), 0.3, colors.HexColor("#cbd5e1")),
            ("VALIGN",     (0,0), (-1,-1), "TOP"),
        ]))
        story.append(a_tbl)
    story.append(PageBreak())

    # ── Action Items ──────────────────────────────────────────────────────
    story.append(Paragraph("Action Items", h2))
    for i, a in enumerate(pack["actions"], start=1):
        story.append(Paragraph(f"<b>{i}.</b> {a}", body))
        story.append(Spacer(1, 0.15*cm))

    doc.build(story)
    return buf.getvalue()


@router.get("/api/export/monthly-pack")
def export_monthly_pack(
    month:  str = Query(..., description="Period in YYYY-MM"),
    format: str = Query("xlsx", description="xlsx | pdf"),
):
    """Monthly Review Pack — multi-sheet workbook or multi-page PDF.

    Sheets / pages: Summary, P&L per Branch, Top 50 Cost Drivers,
    BoM Ingredients, Alerts, Action Items.
    """
    if not month or len(month) != 7 or month[4] != "-":
        raise HTTPException(400, "month must be YYYY-MM")
    fmt = (format or "xlsx").lower()
    if fmt not in ("xlsx", "pdf"):
        raise HTTPException(400, "format must be xlsx or pdf")
    pack = _mpack_collect(month)
    if fmt == "xlsx":
        data = _mpack_render_xlsx(pack)
        media = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        fname = f"oshinei_monthly_pack_{month}.xlsx"
    else:
        try:
            data = _mpack_render_pdf(pack)
        except ImportError as e:
            raise HTTPException(500, f"PDF dependency missing: {e}")
        media = "application/pdf"
        fname = f"oshinei_monthly_pack_{month}.pdf"
    return StreamingResponse(
        _io.BytesIO(data),
        media_type=media,
        headers={"Content-Disposition": f"attachment; filename={fname}"},
    )
