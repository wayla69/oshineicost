#!/usr/bin/env python3
"""
Surin & Ubon Discount Fraud Investigation Report — 50-page version
Usage: python gen_surin_ubon_report.py [--db oshinei.db] [--out ../docs/SURIN_UBON_FRAUD.html]
"""
import sqlite3, openpyxl, sys, os, math, argparse, json
from datetime import datetime, date, timedelta
from collections import defaultdict

DATE_FROM, DATE_TO = "2026-01-01", "2026-05-31"
BRANCH_IDS = {"surin": 24, "ubon": 26}

EMPLOYEES = [
    ("07260005","ผู้จัดการสาขา","surin","ณัจฐวงษ์ ภักดีวัฒนพงษ์","เดียร์",
     "รายงานตารางเวลาการทำงาน_ผู้จัดการสาขาสุรินทร์_กพ69-พค69.xlsx"),
    ("07230013","Supervisor","surin","ราตรี จารัตน์","ตุ๊กตา",
     "รายงานตารางเวลาการทำงาน_Supervisorสุรินทร์_มค69-พค69.xlsx"),
    ("07220004","ผู้ช่วยผู้จัดการ","surin","ภัทรวดี เพ็ญพัฒน์เกษม","ซูกัส",
     "รายงานตารางเวลาการทำงาน_ผู้ช่วยผู้จัดการสุรินทร์_มค69-พค69.xlsx"),
    ("07240009","Cashier","surin","นุชรีย์ เกิดโชค","จ๊ะจ๋า",
     "รายงานตารางเวลาการทำงาน_แคชเชียร์สุรินทร์_มค69-พค69.xlsx"),
    ("02140002","ผู้จัดการสาขา","ubon","สมชาย โทแช่ม","เก่ง",
     "รายงานตารางเวลาการทำงาน_ผู้จัดการสาขาอุบล_มค69-พค69.xlsx"),
    ("02150003","Supervisor","ubon","ศิริวรรณ ศรีทัน","เจมอล์",
     "รายงานตารางเวลาการทำงาน_Supervisorอุบล_มค69-พค69.xlsx"),
    ("02150002","ผู้ช่วยผู้จัดการ","ubon","กาญจนา เจริญยิ่ง","โอ๋",
     "รายงานตารางเวลาการทำงาน_ผู้ช่วยผู้จัดการโอ๋อุบล_มค69-พค69.xlsx"),
    ("08140001","ผู้ช่วยผู้จัดการ","ubon","สร้อยเพชร ไกยพันธ์","ส้ม",
     "รายงานตารางเวลาการทำงาน_ผู้ช่วยผู้จัดการส้มอุบล_มค69-พค69.xlsx"),
    ("02220001","Cashier","ubon","นิธิพร สุวรรณพรม","ตุ๊ต๊ะ",
     "รายงานตารางเวลาการทำงาน_แคชเชียร์อุบล_มค69-พค69.xlsx"),
]

XLSX_DIRS = [
    r"C:\Users\ASUS\OneDrive\Daiki\2026\Fraud",
    os.path.dirname(os.path.abspath(__file__)),
    os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."),
]

# ── Attendance ──────────────────────────────────────────────────────────────
def parse_date_val(v):
    if v is None: return None
    if isinstance(v, (datetime, date)):
        return (v if isinstance(v, datetime) else datetime(v.year, v.month, v.day)).strftime("%Y-%m-%d")
    s = str(v).strip()
    for fmt in ["%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y"]:
        try: return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except: pass
    return None

def load_attendance():
    att = {}
    for eid, role, branch, fullname, nick, fname in EMPLOYEES:
        att[eid] = {}
        for d in XLSX_DIRS:
            path = os.path.join(d, fname)
            if os.path.exists(path):
                wb = openpyxl.load_workbook(path, data_only=True)
                for ws in wb.worksheets:
                    for row in ws.iter_rows(values_only=True):
                        dt = parse_date_val(row[1] if len(row) > 1 else None)
                        status = str(row[2]).strip() if len(row) > 2 and row[2] else ""
                        if dt and DATE_FROM <= dt <= DATE_TO and status:
                            att[eid][dt] = ("หยุด" not in status and "ทำงาน" in status)
                break
    return att

# ── DB ──────────────────────────────────────────────────────────────────────
W = f"substr(sold_at,1,10) BETWEEN '{DATE_FROM}' AND '{DATE_TO}'"

def load_db(db_path):
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    data = {}

    data["chain_disc_pct"] = con.execute(
        f"SELECT round(100.0*sum(discount)/nullif(sum((total_inc_vat-COALESCE(service_charge,0))/1.07+discount),0),2) FROM sales_transaction WHERE {W}"
    ).fetchone()[0] or 0

    for branch, bid in BRANCH_IDS.items():
        b = {}
        s = con.execute(f"SELECT count(*) c, round(sum(discount)) disc FROM sales_transaction WHERE branch_id=? AND {W} AND discount>0",(bid,)).fetchone()
        tot = con.execute(f"SELECT count(*) c, round(sum((total_inc_vat-COALESCE(service_charge,0))/1.07+discount)) rev FROM sales_transaction WHERE branch_id=? AND {W}",(bid,)).fetchone()
        b["disc_bills"]=s[0]; b["disc_total"]=s[1] or 0
        b["total_bills"]=tot[0]; b["total_rev"]=tot[1] or 0
        b["disc_pct"]=round(100*b["disc_total"]/b["total_rev"],2) if b["total_rev"] else 0

        b["by_user"] = [(r["cashier"],r["n"],r["disc"]) for r in con.execute(
            f"SELECT cashier, count(*) n, round(sum(discount)) disc FROM sales_transaction WHERE branch_id=? AND {W} AND discount>0 GROUP BY cashier ORDER BY disc DESC",(bid,))]

        b["by_month"] = [(r["mo"],r["n"],r["disc"]) for r in con.execute(
            f"SELECT strftime('%Y-%m',sold_at) mo, count(*) n, round(sum(discount)) disc FROM sales_transaction WHERE branch_id=? AND {W} AND discount>0 GROUP BY mo ORDER BY mo",(bid,))]

        b["month_by_user"] = [(r["mo"],r["cashier"],r["n"],r["disc"]) for r in con.execute(
            f"SELECT strftime('%Y-%m',sold_at) mo, cashier, count(*) n, round(sum(discount)) disc FROM sales_transaction WHERE branch_id=? AND {W} AND discount>0 GROUP BY mo,cashier ORDER BY mo,disc DESC",(bid,))]

        # Daily total
        b["daily"] = {}
        for r in con.execute(f"SELECT substr(sold_at,1,10) d, count(*) n, round(sum(discount)) disc, round(sum((total_inc_vat-COALESCE(service_charge,0))/1.07+discount)) rev FROM sales_transaction WHERE branch_id=? AND {W} AND discount>0 GROUP BY d",(bid,)):
            b["daily"][r["d"]] = {"n":r["n"],"disc":r["disc"],"rev":r["rev"]}

        # Daily by cashier user
        b["daily_user"] = defaultdict(dict)
        for r in con.execute(f"SELECT substr(sold_at,1,10) d, cashier, count(*) n, round(sum(discount)) disc FROM sales_transaction WHERE branch_id=? AND {W} AND discount>0 GROUP BY d,cashier",(bid,)):
            b["daily_user"][r["d"]][r["cashier"]] = {"n":r["n"],"disc":r["disc"]}

        b["by_dow"] = [(r[0],r[1],r[2]) for r in con.execute(
            f"SELECT CASE cast(strftime('%w',sold_at) as int) WHEN 0 THEN 'อา.' WHEN 1 THEN 'จ.' WHEN 2 THEN 'อ.' WHEN 3 THEN 'พ.' WHEN 4 THEN 'พฤ.' WHEN 5 THEN 'ศ.' WHEN 6 THEN 'ส.' END, count(*) n, round(sum(discount)) disc FROM sales_transaction WHERE branch_id=? AND {W} AND discount>0 GROUP BY strftime('%w',sold_at) ORDER BY strftime('%w',sold_at)",(bid,))]

        b["by_hour"] = [(r[0],r[1],r[2]) for r in con.execute(
            f"SELECT substr(sold_at,12,2) hr, count(*) n, round(sum(discount)) disc FROM sales_transaction WHERE branch_id=? AND {W} AND discount>0 GROUP BY hr ORDER BY hr",(bid,))]

        b["fixed_amounts"] = [(r[0],r[1]) for r in con.execute(
            f"SELECT round(discount) amt, count(*) n FROM sales_transaction WHERE branch_id=? AND {W} AND discount>0 GROUP BY round(discount) ORDER BY n DESC LIMIT 15",(bid,))]

        b["top_bills"] = [(r["d"],r["t"],r["cashier"],r["disc"],r["base"],r["pct"],r["cust"]) for r in con.execute(
            f"SELECT substr(sold_at,1,10) d, substr(sold_at,12,5) t, cashier, round(discount) disc, round((total_inc_vat-COALESCE(service_charge,0))/1.07+discount) base, round(100.0*discount/((total_inc_vat-COALESCE(service_charge,0))/1.07+discount)) pct, customer_count cust FROM sales_transaction WHERE branch_id=? AND {W} AND discount>0 ORDER BY discount DESC LIMIT 20",(bid,))]

        b["big_bills"] = [(r["d"],r["t"],r["cashier"],r["disc"],r["base"],r["pct"],r["cust"]) for r in con.execute(
            f"SELECT substr(sold_at,1,10) d, substr(sold_at,12,5) t, cashier, round(discount) disc, round((total_inc_vat-COALESCE(service_charge,0))/1.07+discount) base, round(100.0*discount/((total_inc_vat-COALESCE(service_charge,0))/1.07+discount)) pct, customer_count cust FROM sales_transaction WHERE branch_id=? AND {W} AND discount>=1000 ORDER BY d, disc DESC",(bid,))]

        data[branch] = b

    con.close()
    return data

# ── Stats ───────────────────────────────────────────────────────────────────
def mean(xs): return sum(xs)/len(xs) if xs else 0
def stdev(xs):
    if len(xs)<2: return 0
    m=mean(xs); return math.sqrt(sum((x-m)**2 for x in xs)/(len(xs)-1))
def variance(xs): return stdev(xs)**2

def norm_cdf(z):
    return 0.5*(1+math.erf(z/math.sqrt(2)))

def welch_t(a, b):
    if not a or not b or len(a)<2 or len(b)<2: return (0.0, 1.0, 0, 0)
    va, vb = variance(a), variance(b)
    na, nb = len(a), len(b)
    se2 = va/na + vb/nb
    if se2<=0: return (0.0,1.0,na,nb)
    t = (mean(a)-mean(b))/math.sqrt(se2)
    df = se2**2 / ((va/na)**2/max(na-1,1)+(vb/nb)**2/max(nb-1,1))
    p = 2*(1-norm_cdf(abs(t))) if df>=30 else min(1.0,max(0.0001,2*(1-norm_cdf(abs(t)*math.sqrt(df/(df+5))))))
    return (round(t,3), round(min(1.0,max(0.0001,p)),4), na, nb)

def pearson_r(xs, ys):
    n=min(len(xs),len(ys))
    if n<3: return 0
    xs,ys=xs[:n],ys[:n]; mx,my=mean(xs),mean(ys)
    num=sum((x-mx)*(y-my) for x,y in zip(xs,ys))
    den=math.sqrt(sum((x-mx)**2 for x in xs)*sum((y-my)**2 for y in ys))
    return round(num/den,3) if den else 0

def build_corr(att, db_data):
    results=[]
    for eid,role,branch,fullname,nick,_ in EMPLOYEES:
        bdata=db_data[branch]
        emp=att.get(eid,{})
        all_days=bdata["daily"]
        du=bdata["daily_user"]

        worked_discs=[all_days[d]["disc"] for d in emp if emp[d] and d in all_days]
        off_discs=[all_days[d]["disc"] for d in emp if not emp[d] and d in all_days]
        t,p,nw,no=welch_t(worked_discs,off_discs)
        common=sorted(d for d in emp if d in all_days)
        r=pearson_r([1 if emp[d] else 0 for d in common],[all_days[d]["disc"] for d in common])

        # POS account analysis: on days worked, which user was dominant?
        acc_worked=defaultdict(lambda:[0,0])  # user -> [days,total_disc]
        for d in emp:
            if emp[d] and d in du:
                for usr,udata in du[d].items():
                    acc_worked[usr][0]+=1
                    acc_worked[usr][1]+=udata["disc"]
        acc_off=defaultdict(lambda:[0,0])
        for d in emp:
            if not emp[d] and d in du:
                for usr,udata in du[d].items():
                    acc_off[usr][0]+=1
                    acc_off[usr][1]+=udata["disc"]

        # Top high-disc days while working
        top_days=sorted(
            [(d,all_days[d]["disc"],dict(du.get(d,{}))) for d in emp if emp[d] and d in all_days],
            key=lambda x:-x[1])[:10]

        off_days_with_disc=[d for d in emp if not emp[d] and d in all_days and all_days[d]["disc"]>0]

        results.append({
            "eid":eid,"nick":nick,"fullname":fullname,"role":role,"branch":branch,
            "worked_days":len([d for d in emp if emp[d]]),
            "off_days":len([d for d in emp if not emp[d]]),
            "att_days":len(emp),
            "worked_disc_mean":round(mean(worked_discs)),
            "off_disc_mean":round(mean(off_discs)),
            "worked_disc_total":round(sum(worked_discs)),
            "off_disc_total":round(sum(off_discs)),
            "off_days_with_disc":len(off_days_with_disc),
            "off_days_list":off_days_with_disc[:15],
            "t":t,"p":p,"r":r,
            "lift":round(mean(worked_discs)/mean(off_discs),2) if mean(off_discs)>0 else 9.9,
            "acc_worked":dict(acc_worked),
            "acc_off":dict(acc_off),
            "top_days":top_days,
            "worked_discs":worked_discs,
            "off_discs":off_discs,
            "att":emp,
        })
    return results

# ── HTML helpers ─────────────────────────────────────────────────────────────
def fmt(n,pre="฿"):
    if n is None: return "-"
    n=float(n)
    if abs(n)>=1_000_000: return f"{pre}{n/1_000_000:.2f}M"
    if abs(n)>=1_000: return f"{pre}{n:,.0f}"
    return f"{pre}{n:.0f}"

def badge(lbl,color="#1e3a5f"):
    return f'<span style="background:{color};color:#fff;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:700">{lbl}</span>'

def risk_badge(pct,chain=1.52):
    r=pct/chain if chain else 0
    if r>=2.5: return badge("⚠ สูงมาก","#dc2626")
    if r>=1.8: return badge("▲ สูง","#ea580c")
    if r>=1.2: return badge("~ ปานกลาง","#d97706")
    return badge("✓ ปกติ","#16a34a")

def p_badge(p):
    if p<0.01: return badge("p<0.01 ***","#dc2626")
    if p<0.05: return badge(f"p={p:.3f} **","#ea580c")
    if p<0.10: return badge(f"p={p:.3f} *","#d97706")
    return badge(f"p={p:.3f} n.s.","#6b7280")

def lift_color(x):
    if x>=2.0: return "#dc2626"
    if x>=1.5: return "#ea580c"
    if x>=1.2: return "#d97706"
    return "#374151"

def bar_row(label,val,max_val,color="#1e3a5f",val_fmt=None):
    pct=min(100,100*val/max_val) if max_val>0 else 0
    vstr=val_fmt if val_fmt else fmt(val)
    return f'<div style="display:flex;align-items:center;gap:8px;margin-bottom:5px"><div style="width:70px;font-size:12px;text-align:right;flex-shrink:0">{label}</div><div style="flex:1;background:#e5e7eb;border-radius:3px;height:18px;overflow:hidden"><div style="width:{pct:.0f}%;background:{color};height:100%;border-radius:3px"></div></div><div style="width:60px;font-size:12px;font-weight:600">{vstr}</div></div>'

# Calendar mini-view: 5-week grid per month
MONTH_TH = {"2026-01":"มกราคม","2026-02":"กุมภาพันธ์","2026-03":"มีนาคม","2026-04":"เมษายน","2026-05":"พฤษภาคม"}
MONTH_SHORT = {"2026-01":"ม.ค.","2026-02":"ก.พ.","2026-03":"มี.ค.","2026-04":"เม.ย.","2026-05":"พ.ค."}

def calendar_month(ym, emp_att, daily_disc):
    y,m=int(ym[:4]),int(ym[5:])
    first=date(y,m,1)
    dow0=first.weekday()  # 0=Mon
    days_in_month=32
    for d in range(28,33):
        try: date(y,m,d); days_in_month=d
        except: break
    cells=[""]*(dow0)+[str(i) for i in range(1,days_in_month+1)]
    while len(cells)%7: cells.append("")
    rows=[]
    for i in range(0,len(cells),7):
        row=cells[i:i+7]
        row_html=""
        for cell in row:
            if not cell:
                row_html+='<td style="width:26px;height:28px"></td>'
                continue
            dt=f"{ym}-{int(cell):02d}"
            is_worked=emp_att.get(dt,None)
            disc=daily_disc.get(dt,{}).get("disc",0) if daily_disc else 0
            if disc>=5000: dc="#fde2e2"; tc="#b91c1c"
            elif disc>=2000: dc="#fde6d4"; tc="#b45309"
            elif disc>=500: dc="#fbf3c7"; tc="#a16207"
            elif disc>0: dc="#e2f5e6"; tc="#15803d"
            else: dc="#f7f9fb"; tc="#94a3b8"
            if is_worked is True:
                border="2px solid #1e3a5f"; bg=dc
            elif is_worked is False:
                border="1px dashed #cbd5e1"; bg="#eef2f6"; tc="#94a3b8"
            else:
                border="1px solid #e2e8f0"; bg=dc
            disc_str=f'<div style="font-size:7px;line-height:1;color:{tc}">{fmt(disc) if disc>0 else ""}</div>'
            row_html+=f'<td style="width:26px;height:28px;text-align:center;vertical-align:top;border:{border};border-radius:2px;background:{bg};padding:1px"><div style="font-size:9px;font-weight:600;line-height:1.1">{cell}</div>{disc_str}</td>'
        rows.append(f"<tr>{row_html}</tr>")
    hdr=''.join(f'<th style="font-size:8px;text-align:center;color:#94a3b8;padding:1px;background:none;border:none">{d}</th>' for d in ["จ","อ","พ","พฤ","ศ","ส","อา"])
    return f'<div class="cal-month" style="margin-bottom:8px"><div style="font-size:11.5px;font-weight:700;color:#1e3a5f;margin-bottom:3px">{MONTH_TH.get(ym,ym)}</div><table style="border-collapse:separate;border-spacing:1.5px;width:auto;margin:0"><thead><tr>{hdr}</tr></thead><tbody>{"".join(rows)}</tbody></table></div>'

# ── CSS ──────────────────────────────────────────────────────────────────────
CSS = """
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Sarabun',sans-serif;font-size:12.5px;line-height:1.5;color:#1f2937;background:#e7eaee;print-color-adjust:exact;-webkit-print-color-adjust:exact}
:root{--ink:#1e3a5f;--ink2:#2c4a70;--muted:#64748b;--line:#dde3ea;--rd:#b91c1c;--am:#b45309;--em:#15803d;--bl:#1e40af}
/* ---------- COVER (flat, formal navy) ---------- */
.cover{width:210mm;min-height:297mm;margin:0 auto 20px;background:#1e3a5f;display:flex;flex-direction:column;justify-content:center;padding:30mm 24mm;color:#fff;page-break-after:always}
.cover-rule{width:54px;height:4px;background:#e0a93c;margin-bottom:22px}
.cover-tag{font-size:11px;letter-spacing:3px;text-transform:uppercase;color:#9db4cf;margin-bottom:18px}
.cover-title{font-size:40px;font-weight:800;line-height:1.12;margin-bottom:16px}
.cover-sub{font-size:17px;font-weight:300;color:#c7d4e3;margin-bottom:34px;max-width:150mm}
.cover-kpis{display:grid;grid-template-columns:repeat(2,1fr);gap:14px;margin-bottom:30px;max-width:150mm}
.ckpi{background:#27466b;border:1px solid #3a5a82;border-radius:8px;padding:16px 18px}
.ckpi-val{font-size:25px;font-weight:800;color:#efc46a}
.ckpi-lbl{font-size:11.5px;color:#aebfd2;margin-top:3px}
.cover-foot{font-size:10.5px;color:#8ba0ba;border-top:1px solid #3a5a82;padding-top:16px;margin-top:30px;display:flex;justify-content:space-between;flex-wrap:wrap;gap:6px}
/* ---------- PAGE / SECTION ---------- */
.page{width:210mm;min-height:297mm;margin:0 auto 20px;background:#fff;box-shadow:0 2px 14px rgba(15,30,55,.10)}
.sec-hdr{border-bottom:2.5px solid var(--ink);padding:0 0 8px;margin-bottom:16px;display:flex;justify-content:space-between;align-items:baseline}
.sec-hdr-title{font-size:15px;font-weight:700;color:var(--ink)}
.sec-hdr-brand{font-size:9.5px;color:#9aa7b6;letter-spacing:1px}
.sec-body{padding:16mm 14mm}
.sec-label{font-size:9.5px;font-weight:700;color:var(--muted);letter-spacing:2px;text-transform:uppercase;margin-bottom:3px}
.sec-title{font-size:18px;font-weight:700;color:var(--ink);margin-bottom:6px}
.sec-desc{font-size:12px;color:var(--muted);margin-bottom:16px}
h-spacer{display:block;height:8px}
/* ---------- KPI CARDS ---------- */
.kpi-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:16px}
.kpi3{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-bottom:16px}
.kpi{background:#f7f9fb;border:1px solid var(--line);border-radius:7px;padding:12px 13px;page-break-inside:avoid}
.kpi-val{font-size:22px;font-weight:800;color:var(--ink)}
.kpi-val.red{color:var(--rd)}.kpi-val.am{color:var(--am)}.kpi-val.em{color:var(--em)}
.kpi-lbl{font-size:10.5px;color:var(--muted);margin-top:2px;line-height:1.35}
/* ---------- TABLES ---------- */
table{width:100%;border-collapse:collapse;font-size:11.5px;margin-bottom:14px}
th{background:#eef2f6;color:#334155;font-weight:700;padding:7px 9px;text-align:left;border-bottom:1.5px solid #cdd6e0;font-size:10.5px}
td{padding:6px 9px;border-bottom:1px solid #eef1f5;vertical-align:middle}
tr{page-break-inside:avoid}
.num{text-align:right;font-variant-numeric:tabular-nums;font-weight:600}
.center{text-align:center}
code{font-family:'Sarabun',monospace;background:#eef2f6;padding:1px 5px;border-radius:3px;font-size:11px;color:#2c4a70}
/* ---------- LAYOUT ---------- */
.two-col{display:grid;grid-template-columns:1fr 1fr;gap:18px;margin-bottom:14px}
.three-col{display:grid;grid-template-columns:1fr 1fr 1fr;gap:14px}
/* ---------- CALLOUTS (light tint + left accent) ---------- */
.alert{border-radius:5px;padding:11px 14px;margin-bottom:12px;border-left:3px solid;font-size:11.5px;line-height:1.55;page-break-inside:avoid}
.alert-red{background:#fdf2f2;border-color:var(--rd);color:#7f1d1d}
.alert-am{background:#fdf8ee;border-color:var(--am);color:#7c4710}
.alert-em{background:#f1faf3;border-color:var(--em);color:#14532d}
.alert-blue{background:#f0f5fc;border-color:var(--bl);color:#1e3a8a}
/* ---------- PERSON HEADER (flat) ---------- */
.person-hdr{background:#27466b;color:#fff;border-radius:8px;padding:16px 20px;margin-bottom:14px;display:flex;justify-content:space-between;align-items:center;page-break-inside:avoid}
.person-name{font-size:20px;font-weight:800}
.person-nick{font-size:14px;color:#c7d4e3}
.person-role{font-size:12px;color:#aebfd2;margin-top:3px}
.person-eid{font-size:10.5px;color:#8ba0ba;font-family:monospace}
.person-stats{display:grid;grid-template-columns:repeat(5,1fr);gap:8px;margin-bottom:12px}
.pstat{background:#f7f9fb;border:1px solid var(--line);border-radius:6px;padding:9px;text-align:center;page-break-inside:avoid}
.pstat-val{font-size:17px;font-weight:800;color:var(--ink)}
.pstat-lbl{font-size:9.5px;color:var(--muted);margin-top:2px}
/* ---------- TOC / VERDICT / FLAGS ---------- */
.toc-item{display:flex;justify-content:space-between;padding:5px 0;border-bottom:1px dotted var(--line);font-size:12px}
.verdict-box{border-radius:6px;padding:12px 14px;border:1px solid;border-left-width:3px;margin-top:10px;page-break-inside:avoid}
.flag-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:12px}
.flag{border-radius:6px;padding:10px 12px;border:1px solid;border-left-width:3px;page-break-inside:avoid}
.flag-red{background:#fdf2f2;border-color:#e6b3b3;border-left-color:var(--rd)}
.flag-am{background:#fdf8ee;border-color:#ead9b6;border-left-color:var(--am)}
.flag-em{background:#f1faf3;border-color:#bfe3c7;border-left-color:var(--em)}
/* ---------- EXEC SUMMARY ---------- */
.bluf{border:1px solid var(--line);border-left:4px solid var(--ink);border-radius:6px;padding:14px 16px;margin-bottom:14px;background:#f7f9fb;page-break-inside:avoid}
.mo-card{border:1px solid var(--line);border-radius:7px;padding:12px 14px;margin-bottom:10px;page-break-inside:avoid}
.mo-head{display:flex;justify-content:space-between;align-items:baseline;margin-bottom:5px}
.mo-branch{font-size:15px;font-weight:800;color:var(--ink)}
.sev{font-size:10px;font-weight:700;padding:2px 9px;border-radius:10px;color:#fff;white-space:nowrap}
.row-2{display:grid;grid-template-columns:1fr 1fr;gap:14px}
/* ---------- PRINT ---------- */
@media print{
  body{background:#fff}
  .cover{margin:0;min-height:auto;box-shadow:none;page-break-after:always;-webkit-print-color-adjust:exact;print-color-adjust:exact}
  .page{width:auto;min-height:0;margin:0;box-shadow:none;page-break-after:always}
  .sec-body{padding:0}
  .page:last-of-type{page-break-after:auto}
  table,.kpi,.alert,.flag,.pstat,.mo-card,.verdict-box,.person-hdr,.cal-month,.bluf{page-break-inside:avoid}
  thead{display:table-header-group}
}
@page{size:A4;margin:14mm 13mm}
"""

# ── REPORT BUILDER ──────────────────────────────────────────────────────────
def generate(att, db_data, robust=None):
    sd=db_data["surin"]; ud=db_data["ubon"]
    chain=db_data["chain_disc_pct"]
    corr=build_corr(att,db_data)
    surin_corr=[c for c in corr if c["branch"]=="surin"]
    ubon_corr=[c for c in corr if c["branch"]=="ubon"]
    # robust layer: revenue-normalized disc%, permutation test, Bonferroni, confound verdicts
    rb_people={p["eid"]:p for p in robust["people"]} if robust else {}
    rb_branch=robust["branches"] if robust else {}
    bonf=robust["bonferroni"] if robust else {"n_tests":0,"threshold":0.05}
    now=datetime.now().strftime("%d %b %Y")
    months=["2026-01","2026-02","2026-03","2026-04","2026-05"]

    sd_excess=round((sd["disc_pct"]/100-chain/100)*sd["total_rev"])
    ud_excess=round((ud["disc_pct"]/100-chain/100)*ud["total_rev"])

    parts=[]
    def add(h): parts.append(h)

    # ── HEAD ────────────────────────────────────────────────────────────────
    add(f"""<!DOCTYPE html><html lang="th"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>รายงานตรวจสอบส่วนลด สุรินทร์ & อุบลฯ 2026 — ฉบับละเอียด</title>
<link href="https://fonts.googleapis.com/css2?family=Sarabun:wght@300;400;600;700;800&display=swap" rel="stylesheet">
<style>{CSS}</style></head><body>""")

    # ── shared exec-summary figures ──────────────────────────────────────────
    master_pct=robust["peer"]["by_type"].get("master",{}).get("pct",chain) if robust else chain
    franch_pct=robust["peer"]["by_type"].get("franchise",{}).get("pct",0) if robust else 0
    udon=robust["udon"] if robust else {"pct":0,"disc":0,"cash_signature":{"lift":0}}
    sd_cash=robust["branches"]["surin"]["cash_signature"]["lift"] if robust else 0
    ud_cash=robust["branches"]["ubon"]["cash_signature"]["lift"] if robust else 0
    sd_coocc=robust["branches"]["surin"]["alternation"]["cooccurrence_rate"] if robust else 0
    ub_buck=robust["branches"]["ubon"]["disc_pct_buckets"] if robust else []
    to_share=round(100*(next((b['n'] for b in ub_buck if b['cashier']=='Take Order'),0))/(sum(b['n'] for b in ub_buck) or 1)) if ub_buck else 0
    to_high=next((b['high_n'] for b in ub_buck if b['cashier']=='Take Order'),0)
    sd_exm=max(0,round((sd["disc_pct"]/100-master_pct/100)*sd["total_rev"]))
    ud_exm=max(0,round((ud["disc_pct"]/100-master_pct/100)*ud["total_rev"]))
    # POLICY ceiling (normative bar — NOT the peer average). Owner policy: ≤2.25-2.5%.
    POLICY=2.5; POLICY_STRICT=2.25
    udon_pct=robust["udon"]["pct"] if robust else 0; udon_rev=round((robust["udon"]["disc"] or 0)/((udon_pct or 1)/100)) if robust and udon_pct else 0
    sd_exp=max(0,round((sd["disc_pct"]-POLICY)/100*sd["total_rev"]))
    ud_exp=max(0,round((ud["disc_pct"]-POLICY)/100*ud["total_rev"]))
    udon_exp=max(0,round((udon_pct-POLICY)/100*udon_rev)) if robust else 0
    # FACT: discount attribution by channel (overturns 'no reason' assumption)
    sd_att=robust["branches"]["surin"]["attribution"] if robust else {}
    ub_att=robust["branches"]["ubon"]["attribution"] if robust else {}
    ud_att=robust["udon"]["attribution"] if robust else {}
    sd_neither_pct=round(100*sd_att.get("neither_n",0)/max(sd_att.get("disc_bills",1),1)) if robust else 0
    # Ubon special-coupon covers (plausibility)
    ub_eld=next((c for c in robust["branches"]["ubon"]["coupons"] if c["sid"]==35),{"bills":0,"covers":0,"amt":0}) if robust else {"bills":0,"covers":0,"amt":0}

    # ── COVER (flat, formal) ─────────────────────────────────────────────────
    add(f"""<div class="cover">
<div class="cover-rule"></div>
<div class="cover-tag">Internal Audit · Confidential</div>
<div class="cover-title">รายงานตรวจสอบ<br>ส่วนลดผิดปกติ</div>
<div class="cover-sub">เปรียบเทียบ 3 สาขา master · อุดรธานี · สุรินทร์ · อุบลราชธานี — และรูปแบบการทุจริตที่ต่างกัน</div>
<div class="cover-kpis">
  <div class="ckpi"><div class="ckpi-val">3 รูปแบบ</div><div class="ckpi-lbl">การทุจริต/ช่องโหว่ ต่างกันรายสาขา</div></div>
  <div class="ckpi"><div class="ckpi-val">{fmt(sd['disc_total']+ud['disc_total']+ (udon['disc'] or 0))}</div><div class="ckpi-lbl">ส่วนลดรวม 3 สาขา (5 เดือน)</div></div>
  <div class="ckpi"><div class="ckpi-val">{sd_neither_pct}%</div><div class="ckpi-lbl">บิลส่วนลดสุรินทร์ ไม่ผูกคูปอง/เหตุผล (อุบล/อุดร=0%)</div></div>
  <div class="ckpi"><div class="ckpi-val">1 สาขา</div><div class="ckpi-lbl">พิสูจน์ทุจริตแล้ว (อุดรธานี)</div></div>
</div>
<div class="cover-foot">
  <span>Oshinei Enterprise — ฝ่ายตรวจสอบภายใน</span>
  <span>ข้อมูล {DATE_FROM} ถึง {DATE_TO}</span>
  <span>จัดทำ {now}</span>
</div></div>""")

    # ════════════════════════ EXECUTIVE SUMMARY (3 หน้า) ════════════════════════
    # — หน้า 1: คำตัดสิน (BLUF) —
    add(page_hdr("บทสรุปผู้บริหาร — 1/3 : คำตัดสิน"))
    add(f"""<div class="sec-label">Bottom line up front</div>
<div class="sec-title">3 สาขา 3 รูปแบบการทุจริต ไม่เหมือนกัน</div>
<div class="bluf">ความผิดปกติเรื่องส่วนลดในกลุ่มสาขาที่บริษัทบริหารเอง (master) เป็นจริงและพิสูจน์ได้ — แต่
<strong>แต่ละสาขามีรูปแบบต่างกัน ต้องแก้คนละทาง</strong> การใช้ตัวชี้วัดเดียว (เช่น %ส่วนลดสูง หรือเทียบค่าเฉลี่ยทั้งเครือ) ตัดสินทุกสาขาจะทำให้เข้าใจผิด
รายงานฉบับนี้แยกชัดเจนว่า <strong>อะไรพิสูจน์แล้ว · อะไรเป็นช่องโหว่ที่ต้องสืบต่อ · และอะไรที่ข้อมูลชุดนี้ยังสรุปไม่ได้</strong></div>
<div class="mo-card" style="border-left:4px solid var(--rd)">
<div class="mo-head"><span class="mo-branch">1. อุดรธานี</span><span class="sev" style="background:var(--rd)">พิสูจน์แล้ว · ดำเนินการ</span></div>
<div style="font-size:12px"><strong>ยักเงินสดผ่านคูปองเด็ก:</strong> กดคูปอง<u>ส่วนลดเด็ก</u> (coupon #33) เข้าบิลหลังปิดการขาย แล้วเก็บ<u>เงินสด</u>ส่วนต่าง ทำกับบิลจ่ายเงินสด
<br><span style="color:var(--muted)">หลักฐาน POS: คูปองเด็ก #33 = {next((c['bills'] for c in robust['udon']['coupons'] if c['sid']==33),0):,} บิล ฿{next((c['amt'] for c in robust['udon']['coupons'] if c['sid']==33),0):,.0f} ผ่านบัญชี admin เป็นหลัก · ส่วนลดกระจุกบิลเงินสด {udon['cash_signature']['lift']}x · เจ้าของยืนยัน (สอบทานอิสระแล้วตรงกับข้อมูล)</span></div>
</div>
<div class="mo-card" style="border-left:4px solid var(--am)">
<div class="mo-head"><span class="mo-branch">2. อุบลราชธานี</span><span class="sev" style="background:var(--am)">ความสมเหตุสมผล · ต้องสืบ</span></div>
<div style="font-size:12px"><strong>คูปองสิทธิ์/เงื่อนไข ปริมาณสูง ผ่าน Take Order:</strong> ส่วนลดทุกบิล<u>ผูกคูปอง</u> (มี audit trail) — ผู้สูงอายุ 81+ / เด็ก / ผู้ถือหุ้น / ผู้บริหาร — กดผ่านบัญชี <code>Take Order</code> เป็นส่วนใหญ่
<br><span style="color:var(--muted)">หลักฐาน POS: คูปองผู้สูงอายุ 81+ (#35) = {ub_eld['bills']} บิล แต่ <strong>{ub_eld['covers']:,} ที่นั่ง</strong> เฉลี่ย ฿{round(ub_eld['amt']/max(ub_eld['bills'],1)):,}/บิล (ทั้งโต๊ะได้สิทธิ์ 81+?) — ต้องทดสอบกับหลักฐานคุณสมบัติ</span></div>
</div>
<div class="mo-card" style="border-left:4px solid var(--rd)">
<div class="mo-head"><span class="mo-branch">3. สุรินทร์</span><span class="sev" style="background:var(--rd)">ผิดปกติที่สุด · ต้องสืบ</span></div>
<div style="font-size:12px"><strong>comp ไม่มีเอกสาร ผ่านบัญชีสิทธิ์สูง:</strong> ต่างจากอีก 2 สาขา — ส่วนลด <strong>{sd_neither_pct}%</strong> ของบิล<u>ไม่ผูกทั้งคูปองและเหตุผล</u> กดผ่าน <code>admin</code>+<code>Manager</code> สลับวัน
<br><span style="color:var(--muted)">หลักฐาน POS: {sd_att.get('neither_n',0):,}/{sd_att.get('disc_bills',0):,} บิลไม่มี attribution รวม ฿{sd_att.get('neither_amt',0):,.0f} เฉลี่ย ~฿600/บิล (≈ บุฟเฟ่ต์ 1 ที่) · 2 บัญชี co-occur เพียง {sd_coocc}%</span></div>
</div>
<div class="alert alert-blue" style="margin-top:6px"><strong>สิ่งที่ข้อมูลชุดนี้ยังสรุปไม่ได้:</strong> "ใคร" เป็นผู้กระทำในแต่ละบิล — ระบบบันทึกเป็นชื่อ <em>บัญชี POS</em> (admin/Manager/Take Order) ไม่ใช่ตัวบุคคล · ต้องใช้ POS login log + CCTV + map account→พนักงาน จาก Easy ประกอบ</div>""")
    add('</div></div>')

    # — หน้า 2: หลักฐานสำคัญ + เทียบ peer —
    add(page_hdr("บทสรุปผู้บริหาร — 2/3 : หลักฐานสำคัญ"))
    add(f"""<div class="sec-label">Key evidence at a glance</div>
<div class="sec-title">ตัวเลขสำคัญ และการเทียบให้เป็นธรรม</div>
<div class="alert alert-red"><strong>เพดานนโยบาย ≤ {POLICY}% (ปกติไม่ควรเกิน {POLICY_STRICT}-{POLICY}%) — ทั้ง 3 สาขา master เกินเพดานหมด:</strong>
ค่าเฉลี่ย master ({master_pct}%) <u>ไม่ใช่</u> "เกณฑ์ผ่าน" — มันเองก็เกินเพดาน = over-discounting เชิงระบบของกลุ่ม master ·
ส่วนแฟรนไชส์ (เฉลี่ย {franch_pct}%) อยู่ในเพดาน เพราะจ่ายเงินตัวเอง · ใช้ค่าเฉลี่ย master เป็นบรรทัดฐานคือการ "ยอมรับความผิดปกติเพราะคนอื่นก็ทำ"</div>
<table>
<thead><tr><th>สาขา (master)</th><th class="num">% ส่วนลด</th><th class="num">เกินเพดาน {POLICY}%</th><th class="num">ส่วนเกิน (฿)</th><th class="num">cash-lift</th><th>รูปแบบหลัก</th></tr></thead>
<tbody>
<tr style="background:#fdf2f2"><td><strong>อุดรธานี</strong></td><td class="num" style="color:var(--rd);font-weight:800">{udon['pct']}%</td><td class="num" style="color:var(--rd)">+{round(udon['pct']-POLICY,2)}%</td><td class="num" style="color:var(--rd);font-weight:700">{fmt(udon_exp)}</td><td class="num" style="color:var(--rd);font-weight:800">{udon['cash_signature']['lift']}x</td><td style="font-size:11px">Cash-pocketing (พิสูจน์แล้ว)</td></tr>
<tr style="background:#fdf2f2"><td><strong>สุรินทร์</strong></td><td class="num" style="color:var(--rd);font-weight:700">{sd['disc_pct']}%</td><td class="num" style="color:var(--rd)">+{round(sd['disc_pct']-POLICY,2)}%</td><td class="num" style="color:var(--rd);font-weight:700">{fmt(sd_exp)}</td><td class="num">{sd_cash}x</td><td style="font-size:11px">comp ไม่มีเอกสาร</td></tr>
<tr style="background:#fdf2f2"><td><strong>อุบลราชธานี</strong></td><td class="num" style="color:var(--rd);font-weight:700">{ud['disc_pct']}%</td><td class="num" style="color:var(--rd)">+{round(ud['disc_pct']-POLICY,2)}%</td><td class="num" style="color:var(--rd);font-weight:700">{fmt(ud_exp)}</td><td class="num">{ud_cash}x</td><td style="font-size:11px">คูปองสิทธิ์ปริมาณสูง</td></tr>
</tbody></table>
<div class="row-2">
<div class="alert alert-em" style="margin:0"><strong>cash-signature = เครื่องมือแยกแยะ</strong><br><span style="font-size:11px">ส่วนลดที่ "กระจุกบิลเงินสด" เท่านั้นที่ยักเงินสดได้ · อุดร {udon['cash_signature']['lift']}x = ผิดปกติ · สุรินทร์ {sd_cash}x / อุบลฯ {ud_cash}x = ปกติ → 2 สาขานี้<u>ไม่ใช่</u>การยักเงินสดแบบอุดร (ส่วนลดอยู่บนบัตร/โอน กดเงินสดไม่ได้)</span></div>
<div class="alert alert-am" style="margin:0"><strong>แต่ "ไม่ใช่แบบอุดร" ≠ สะอาด</strong><br><span style="font-size:11px">สุรินทร์/อุบลฯ มี MO ของตัวเอง (ไร้เหตุผล / สิทธิ์กลับหัว) ที่ต้องสืบต่อ — cash-signature แค่ตัด MO เงินสดของอุดรออกเท่านั้น</span></div>
</div>
<table style="margin-top:4px">
<thead><tr><th>ประเด็น</th><th>สถานะ</th></tr></thead>
<tbody>
<tr><td>อุดร = ยักเงินสดผ่านคูปองเด็ก #33 บนบิลเงินสด</td><td style="color:var(--rd);font-weight:700">พิสูจน์แล้ว ✓ (สอบทานข้อมูลตรง)</td></tr>
<tr><td>อุบลฯ = คูปองผู้สูงอายุ81+/เด็ก ปริมาณสูง (covers/บิลผิดสัดส่วน)</td><td style="color:var(--am);font-weight:700">ต้อง vouch หลักฐานคุณสมบัติ</td></tr>
<tr><td>สุรินทร์ = {sd_neither_pct}% ของบิลส่วนลด ไม่มีคูปอง+เหตุผล</td><td style="color:var(--am);font-weight:700">ต้อง vouch เอกสารอนุมัติ + สัมภาษณ์</td></tr>
<tr><td>ระบุตัว "บุคคล" ผู้กระทำ (ทุกสาขา)</td><td style="color:var(--muted);font-weight:700">ยังสรุปไม่ได้ — ต้อง login log + CCTV</td></tr>
</tbody></table>""")
    add('</div></div>')

    # — หน้า 3: แผนปฏิบัติ + ข้อมูลที่ต้องดึงเพิ่ม —
    add(page_hdr("บทสรุปผู้บริหาร — 3/3 : สิ่งที่ต้องทำ"))
    add(f"""<div class="sec-label">Recommended actions</div>
<div class="sec-title">แผนปฏิบัติเรียงตามลำดับความสำคัญ</div>
<table>
<thead><tr><th style="width:6%">#</th><th>การดำเนินการ</th><th style="width:20%">ความเร่งด่วน</th></tr></thead>
<tbody>
<tr><td>1</td><td><strong>อุดรธานี:</strong> ดำเนินการตามผลที่จับได้ + ขยายผลย้อนหลัง</td><td style="color:var(--rd);font-weight:700">ทันที</td></tr>
<tr><td>2</td><td><strong>ระงับสิทธิ์ส่วนลดของบัญชี Take Order ทุกสาขา</strong> (พบใน 8 สาขา ไม่ใช่แค่อุบลฯ)</td><td style="color:var(--rd);font-weight:700">ทันที</td></tr>
<tr><td>3</td><td><strong>เปลี่ยนรหัส + แยกบัญชี admin/Manager สุรินทร์</strong> (กันการแชร์รหัส)</td><td style="color:var(--rd);font-weight:700">ทันที</td></tr>
<tr><td>4</td><td><strong>บังคับเลือกประเภทส่วนลด + ล็อกการแก้บิลหลังปิดการขาย</strong> ใน POS ทุกสาขา</td><td style="color:var(--am);font-weight:700">เร่งด่วน</td></tr>
<tr><td>5</td><td><strong>รัน cash-signature ทุกสาขา</strong> หาสาขาอื่นที่เข้าข่ายแบบอุดร</td><td style="color:var(--am);font-weight:700">เร่งด่วน</td></tr>
<tr><td>6</td><td><strong>สัมภาษณ์ผู้จัดการ/หัวหน้าสุรินทร์</strong> ขอเหตุผลส่วนลดย้อนหลัง + ดึง POS login log</td><td style="color:var(--am);font-weight:700">เร่งด่วน</td></tr>
<tr><td>7</td><td><strong>ตั้ง approval workflow</strong>: ส่วนลด/ประเภทพิเศษ ต้องมีหัวหน้าอนุมัติ + แนบหลักฐาน</td><td style="color:var(--am);font-weight:700">ระยะกลาง</td></tr>
</tbody></table>
<div class="sec-label" style="margin-top:8px">Data to pull next</div>
<div class="sec-title" style="font-size:15px">ข้อมูลที่ต้องดึงเพิ่มเพื่อปิดคดี</div>
<div class="alert alert-blue">
<strong>1. หลักฐานคุณสมบัติผู้รับคูปองสิทธิ์ (อุบลฯ)</strong> — ประเภทผู้สูงอายุ81+/เด็ก/ผู้ถือหุ้น<u>มีบันทึกเป็นคูปองในระบบแล้ว</u> · ต้อง vouch กับหลักฐานจริง (บัตร ปชช./อายุ/ทะเบียนผู้ถือหุ้น) โดยเฉพาะคูปองผู้สูงอายุ81+ ที่ครอบทั้งโต๊ะ
<br><strong>2. POS login / audit log</strong> — ใคร login เป็น admin/Manager/Take Order แต่ละวัน-เวลา (เชื่อมกับเวลาบิลส่วนลด) เพื่อระบุตัวบุคคล
<br><strong>3. CCTV เฉพาะจุด</strong> — เจาะเฉพาะบิลเงินสด+ส่วนลด (signature อุดร) และวันส่วนลดสูงผิดปกติ ไม่ต้องดูทุกบิล</div>
<div class="alert alert-am"><strong>ขอบเขตและข้อจำกัด:</strong> รายงานนี้ใช้ข้อมูลธุรกรรม ม.ค.–พ.ค. 2569 + ตารางกะพนักงาน 9 คน ·
ยืนยันได้ระดับ "สาขาและระบบควบคุม" · การชี้ตัวบุคคลต้องใช้หลักฐาน 3 อย่างข้างต้นประกอบ (รายละเอียดวิธีการและข้อจำกัดอยู่ในภาคผนวก)</div>""")
    add('</div></div>')

    # ── TOC PAGE ──────────────────────────────────────────────────────────────
    add(page_hdr("สารบัญ (Table of Contents)"))
    toc_items = [
        ("","บทสรุปผู้บริหาร (3 หน้า) — คำตัดสิน · หลักฐาน · สิ่งที่ต้องทำ","2-4"),
        ("ส่วนที่ 1","กรอบความเข้าใจ",""),
        ("1","Benchmark ที่เป็นธรรม: Master vs Franchise","6"),
        ("2","กรณีอ้างอิงอุดรธานี (ทุจริตที่จับได้) + Signature เงินสด","7"),
        ("3","★ 3 สาขา 3 รูปแบบทุจริต (อุดร/อุบล/สุรินทร์)","8"),
        ("ส่วนที่ 2","เจาะลึกรายสาขา",""),
        ("4","เปรียบเทียบ KPI กับมาตรฐานทั้งเชน","9"),
        ("5","สาขาสุรินทร์ — POS account · fixed amount · timeline","10-12"),
        ("6","สาขาอุบลราชธานี — Take Order · ส่วนลดพิเศษ · big bills","13-15"),
        ("ภาคผนวก","วิธีการ · ข้อจำกัด · รายบุคคล",""),
        ("ก","ระเบียบวิธี + ตัวแปรกวน + การสลับ account + ข้อจำกัด","16-18"),
        ("ข","ภาพรวมรายบุคคล (raw vs ปรับแก้)","19"),
        ("ค","วิเคราะห์รายบุคคล 9 คน (คนละ 2 หน้า) + ปฏิทิน","20-37"),
        ("ง","สรุปเปรียบเทียบรายสาขา + Correlation ดิบ + Off-day","38-41"),
        ("ส่วนที่ 3","ความเสียหาย และการลงมือ",""),
        ("7","ประมาณการส่วนลดส่วนเกิน (เทียบเพดานนโยบาย ≤2.5%)","42"),
        ("8","ขั้นตอนการตรวจสอบที่แนะนำ (Audit Procedures)","43"),
        ("9","Red Flags & Action Plan","44"),
    ]
    toc_html='<div style="border:1px solid var(--line);border-radius:8px;padding:14px 18px">'
    section_heads={"ส่วนที่ 1","ส่วนที่ 2","ส่วนที่ 3","ภาคผนวก"}
    for num,title,pg in toc_items:
        if num in section_heads:
            toc_html+=f'<div style="margin:10px 0 4px;font-size:10.5px;letter-spacing:1.5px;text-transform:uppercase;color:var(--muted);font-weight:700;border-bottom:1px solid var(--line);padding-bottom:3px">{num} · {title}</div>'
            continue
        toc_html+=f'<div class="toc-item"><span style="display:flex;gap:10px"><span style="color:var(--ink);font-weight:700;min-width:46px">{num}</span><span>{title}</span></span><span style="color:#94a3b8;white-space:nowrap">{pg}</span></div>'
    toc_html+='</div>'
    add(toc_html)
    add('</div></div>')

    # ── PART 1 — FRAMEWORK: 3 patterns + peer benchmark + Udon ────────────────
    if robust:
        _add_peer_benchmark_pages(add, robust, chain)

    # ── PART 2 — KPI BENCHMARK + branch deep dives ────────────────────────────
    add(page_hdr("เปรียบเทียบ KPI — Chain Benchmark"))
    sd_month={r[0]:r[2] for r in sd["by_month"]}
    ud_month={r[0]:r[2] for r in ud["by_month"]}
    max_m=max(list(sd_month.values())+list(ud_month.values()) or [1])
    add(f"""<table><thead><tr><th>สาขา</th><th class="num">ยอดขายรวม</th><th class="num">ส่วนลดรวม</th><th class="num">% ส่วนลด</th><th class="num">เทียบ Chain</th><th>ระดับความเสี่ยง</th></tr></thead><tbody>
<tr style="background:#fef2f2"><td><strong>สุรินทร์</strong></td><td class="num">{fmt(sd['total_rev'])}</td><td class="num" style="color:#dc2626;font-weight:700">{fmt(sd['disc_total'])}</td><td class="num" style="color:#dc2626;font-weight:700">{sd['disc_pct']}%</td><td class="num">{round(sd['disc_pct']/chain,1)}x</td><td>{risk_badge(sd['disc_pct'],chain)}</td></tr>
<tr style="background:#fff7ed"><td><strong>อุบลราชธานี</strong></td><td class="num">{fmt(ud['total_rev'])}</td><td class="num" style="color:#ea580c;font-weight:700">{fmt(ud['disc_total'])}</td><td class="num" style="color:#ea580c;font-weight:700">{ud['disc_pct']}%</td><td class="num">{round(ud['disc_pct']/chain,1)}x</td><td>{risk_badge(ud['disc_pct'],chain)}</td></tr>
<tr style="background:#f0fdf4"><td><strong>Chain Average</strong></td><td class="num">—</td><td class="num">—</td><td class="num" style="color:#16a34a;font-weight:700">{chain}%</td><td class="num">1.0x</td><td>{badge("✓ Baseline","#16a34a")}</td></tr>
</tbody></table>
<div class="two-col">
<div><strong>ส่วนลดรายเดือน — สุรินทร์</strong><div style="height:8px"></div>""")
    for mo in months:
        v=sd_month.get(mo,0)
        add(bar_row(MONTH_SHORT[mo],v,max_m,"#1e3a5f"))
    add(f'</div><div><strong>ส่วนลดรายเดือน — อุบลฯ</strong><div style="height:8px"></div>')
    for mo in months:
        v=ud_month.get(mo,0)
        add(bar_row(MONTH_SHORT[mo],v,max_m,"#ea580c"))
    add('</div></div>')
    add(f"""<div class="alert alert-am"><strong>ข้อเท็จจริงการบันทึก (fact-checked):</strong>
อุบลฯ/อุดร — ส่วนลด<u>ทุกบิลผูกคูปอง</u> (มี audit trail ครบ) · สุรินทร์ — <strong>{sd_neither_pct}%</strong> ของบิลส่วนลด ({sd_att.get('neither_n',0):,}/{sd_att.get('disc_bills',0):,}) ไม่ผูกทั้งคูปองและเหตุผล
→ ปัญหาเอกสารกระจุกที่ <u>สุรินทร์</u> เป็นหลัก (ไม่ใช่ทั้ง 2 สาขาตามที่เคยเข้าใจ) — รายละเอียดดูหน้าเจาะลึกสุรินทร์</div>""")
    add('</div></div>')

    # ── SURIN DEEP DIVE 1: POS Account Pattern ──────────────────────────────
    add(page_hdr("สุรินทร์ — รูปแบบ POS Account: Manager vs admin"))
    surin_user_colors={"Manager":"#1e3a5f","admin":"#dc2626","AccOshinei":"#d97706","adminhq":"#6b7280"}
    surin_total=sd["disc_total"] or 1
    add(f"""<div class="alert alert-red">
<strong>รูปแบบสำคัญ:</strong> ส่วนลดทั้งหมดในสาขาสุรินทร์ผ่านเพียง 2 user คือ <code>Manager</code> ({round(100*sd['by_user'][0][1]/surin_total,1)}%) และ <code>admin</code> ({round(100*sd['by_user'][1][1]/surin_total,1)}%)
ทั้งสองสลับกันวันต่อวัน แทบไม่มีวันที่ทั้งคู่ให้ส่วนลดพร้อมกัน — <strong>บ่งชี้ว่ามีคนเพียง 1-2 คนที่รู้รหัสทั้งสอง account</strong>
</div>
<div class="two-col">
<div>
<strong>สัดส่วน user ตลอด 5 เดือน</strong>
<table style="margin-top:8px">
<thead><tr><th>User</th><th class="num">บิล</th><th class="num">ส่วนลดรวม</th><th class="num">%</th></tr></thead><tbody>""")
    for u,n,d in sd["by_user"]:
        c=surin_user_colors.get(u,"#374151")
        pct=round(100*d/surin_total,1)
        add(f'<tr><td style="color:{c};font-weight:700"><code>{u}</code></td><td class="num">{n:,}</td><td class="num" style="color:{c};font-weight:700">{fmt(d)}</td><td class="num">{pct}%</td></tr>')
    add('</tbody></table></div><div>')
    add('<strong>รายเดือน ตาม user</strong><table style="margin-top:8px"><thead><tr><th>เดือน</th><th>User</th><th class="num">บิล</th><th class="num">ส่วนลด</th></tr></thead><tbody>')
    for mo,u,n,d in sd["month_by_user"]:
        c=surin_user_colors.get(u,"#374151")
        add(f'<tr><td style="font-size:11px">{MONTH_SHORT.get(mo,mo)}</td><td style="color:{c};font-weight:700;font-size:11px"><code>{u}</code></td><td class="num">{n}</td><td class="num" style="color:{c}">{fmt(d)}</td></tr>')
    add('</tbody></table></div></div>')
    add("""<div class="alert alert-am">
<strong>วิธีอ่านผล:</strong> ถ้า Manager ทำงานวันที่มีส่วนลดผ่าน user "Manager" มาก → ผู้จัดการเป็นผู้ดำเนินการ
ถ้า admin ทำงานวันที่มีส่วนลดผ่าน user "admin" มาก → ผู้ที่ใช้ admin account เป็นผู้ดำเนินการ
การวิเคราะห์รายบุคคล (หน้า 11-19) จะแสดงรายละเอียด per-person correlation กับแต่ละ user</div>""")
    add('</div></div>')

    # ── SURIN DEEP DIVE 2: Fixed amounts + Big bills ──────────────────────────
    add(page_hdr("สุรินทร์ — Fixed Amount Patterns & Big Bills"))
    add("""<div class="alert alert-red">
<strong>Fixed Amount Alert:</strong> ฿631 ซ้ำ 272 ครั้ง, ฿694 ซ้ำ 145 ครั้ง, ฿347 ซ้ำ 117 ครั้ง
ตัวเลขเหล่านี้ไม่กลมและซ้ำมากผิดปกติ อาจเป็นราคาต่อหัวของบุฟเฟ่ต์ที่ถูกหักออก
หรือเป็นการกด "ส่วนลด" ซ้ำด้วยจำนวนเดิมทุกครั้ง
</div>
<div class="two-col">
<div>
<strong>จำนวนส่วนลดที่ซ้ำกันมากที่สุด (Top 15)</strong>
<table style="margin-top:8px"><thead><tr><th class="num">฿ ส่วนลด</th><th class="num">ครั้ง</th><th class="num">รวม</th><th>หมายเหตุ</th></tr></thead><tbody>""")
    total_bills=sd["disc_bills"] or 1
    for amt,n in sd["fixed_amounts"]:
        pct=round(100*n/total_bills,1)
        note=""
        if amt==631: note="= ฿631 (×272)"
        elif amt==694: note="= ฿694 (×145)"
        elif amt==347: note="≈ ฿694/2"
        elif amt==1263: note="= ฿631×2"
        elif amt==1389: note="= ฿694×2"
        add(f'<tr><td class="num" style="font-family:monospace;font-weight:700">฿{amt:,.0f}</td><td class="num">{n}</td><td class="num">{fmt(amt*n)}</td><td style="font-size:11px;color:#64748b">{note} ({pct}%)</td></tr>')
    add('</tbody></table></div><div>')
    add('<strong>บิลส่วนลดสูง (≥฿1,000) Top 20</strong>')
    add('<table style="margin-top:8px"><thead><tr><th>วันที่</th><th>เวลา</th><th>User</th><th class="num">ส่วนลด</th><th class="num">%ลด</th><th>คน</th></tr></thead><tbody>')
    for d,t,u,disc,base,pct,cust in sd["big_bills"][:20]:
        c=surin_user_colors.get(u,"#374151")
        add(f'<tr><td style="font-size:11px">{d}</td><td style="font-size:11px">{t}</td><td style="color:{c};font-weight:700;font-size:11px"><code>{u}</code></td><td class="num" style="color:{c};font-weight:700">{fmt(disc)}</td><td class="num">{pct}%</td><td class="num">{cust or "-"}</td></tr>')
    add('</tbody></table></div></div>')
    add('</div></div>')

    # ── SURIN DEEP DIVE 3: DOW + Hour ────────────────────────────────────────
    add(page_hdr("สุรินทร์ — วิเคราะห์ตาม Day of Week & ช่วงเวลา"))
    max_sd_dow=max(r[2] for r in sd["by_dow"]) if sd["by_dow"] else 1
    max_sd_hr=max(r[2] for r in sd["by_hour"]) if sd["by_hour"] else 1
    add('<div class="two-col"><div>')
    add('<strong>ส่วนลดตาม Day of Week</strong><div style="height:10px">')
    for dow,n,disc in sd["by_dow"]:
        c="#dc2626" if dow in ("อา.","ส.") else "#1e3a5f"
        add(bar_row(dow,disc,max_sd_dow,c))
    add('</div></div><div><strong>ส่วนลดตาม ชั่วโมง</strong><div style="height:10px">')
    for hr,n,disc in sd["by_hour"]:
        c="#dc2626" if hr in ("19","20") else "#1e3a5f"
        add(bar_row(f"{hr}:00",disc,max_sd_hr,c))
    add('</div></div></div>')
    add(f"""<div class="alert alert-am" style="margin-top:8px">
<strong>วันเสาร์-อาทิตย์:</strong> ส่วนลดรวม ฿{sd["by_dow"][0][2]+sd["by_dow"][6][2]:,.0f} ({round(100*(sd["by_dow"][0][2]+sd["by_dow"][6][2])/sd["disc_total"])}% ของทั้งหมด)
วันธรรมดาเฉลี่ย ฿{round(sum(r[2] for r in sd["by_dow"][1:6])/5):,}/วัน vs วันหยุดเฉลี่ย ฿{round((sd["by_dow"][0][2]+sd["by_dow"][6][2])/2):,}/วัน
ช่วง 19:00-21:00 น. มีส่วนลด {fmt(sum(r[2] for r in sd["by_hour"] if r[0] in ("19","20","21")))} ({round(100*sum(r[2] for r in sd["by_hour"] if r[0] in ("19","20","21"))/sd["disc_total"])}% ของทั้งหมด)
</div>""")
    add('</div></div>')

    # ── UBON DEEP DIVE 1: Take Order authority ────────────────────────────────
    add(page_hdr("อุบลฯ — Take Order: สิทธิ์ที่ไม่ควรมี"))
    ubon_colors={"Take Order":"#dc2626","Cashier":"#1e3a5f","admin":"#ea580c","Cashier1":"#d97706","Manager":"#6b7280"}
    ubon_total=ud["disc_total"] or 1
    add(f"""<div class="alert alert-red">
<strong>🚨 ปัญหาสิทธิ์ POS:</strong> user <code>Take Order</code> ให้ส่วนลดรวม {fmt(ud['by_user'][0][2])} ({round(100*ud['by_user'][0][2]/ubon_total,1)}% ของทั้งหมด)
ตามมาตรฐาน POS พนักงานรับออเดอร์ควรมีสิทธิ์แค่รับออเดอร์เท่านั้น ไม่ควรมีสิทธิ์ approve ส่วนลด
การที่ Take Order user ให้ส่วนลดได้หมายความว่า: (1) ระบบ permission ตั้งผิด หรือ (2) มีการใช้ account ร่วมกันโดยคนที่มีสิทธิ์
</div>
<div class="two-col">
<div>
<strong>ส่วนลดตาม User ทั้งหมด</strong>
<table style="margin-top:8px"><thead><tr><th>User</th><th class="num">บิล</th><th class="num">ส่วนลดรวม</th><th class="num">%</th></tr></thead><tbody>""")
    for u,n,d in ud["by_user"]:
        c=ubon_colors.get(u,"#374151")
        pct=round(100*d/ubon_total,1)
        add(f'<tr><td style="color:{c};font-weight:700"><code>{u}</code></td><td class="num">{n:,}</td><td class="num" style="color:{c};font-weight:700">{fmt(d)}</td><td class="num">{pct}%</td></tr>')
    add('</tbody></table></div><div>')
    add('<strong>รายเดือน ตาม User</strong><table style="margin-top:8px"><thead><tr><th>เดือน</th><th>User</th><th class="num">บิล</th><th class="num">ส่วนลด</th></tr></thead><tbody>')
    for mo,u,n,d in ud["month_by_user"]:
        c=ubon_colors.get(u,"#374151")
        add(f'<tr><td style="font-size:11px">{MONTH_SHORT.get(mo,mo)}</td><td style="color:{c};font-weight:700;font-size:11px"><code>{u}</code></td><td class="num">{n}</td><td class="num" style="color:{c}">{fmt(d)}</td></tr>')
    add('</tbody></table></div></div>')
    add('</div></div>')

    # ── UBON DEEP DIVE 2: coupon-series FACT breakdown ─────────────────────────
    add(page_hdr("อุบลฯ — คูปองประเภทสิทธิ์/เงื่อนไข (ข้อเท็จจริงจาก POS)"))
    add("""<div class="sec-label">Coupon-series breakdown (fact-checked)</div>
<div class="sec-title" style="font-size:16px">ส่วนลดทุกบิล "ผูกคูปอง" — มี audit trail แต่ปริมาณ/ผู้กดต้องตรวจ</div>
<div class="alert alert-blue">
<strong>แก้ความเข้าใจเดิม:</strong> ส่วนลดอุบลฯ <u>ไม่ได้</u> "ไม่ระบุเหตุผล" — ทุกบิล (2,412/2,412) ผูก <code>coupon_series_id</code> ในระบบ
ประเภทผู้บริหาร/ผู้ถือหุ้น/ผู้สูงอายุ/เด็ก จึงตรวจสอบย้อนหลังได้ · ประเด็นที่ต้องสืบคือ <strong>(1) ความสมเหตุสมผลของปริมาณ</strong> และ <strong>(2) บัญชีที่กด (Take Order)</strong></div>""")
    if robust:
        cps=robust["branches"]["ubon"]["coupons"]
        add('<table><thead><tr><th>คูปอง (coupon series)</th><th class="num">บิล</th><th class="num">ที่นั่ง</th><th class="num">ที่นั่ง/บิล</th><th class="num">฿ ส่วนลด</th><th>บัญชีหลัก</th><th class="center">สิทธิ์/เงื่อนไข</th></tr></thead><tbody>')
        for c in cps[:9]:
            cpb=round(c["covers"]/c["bills"],1) if c["bills"] else 0
            sp=badge("ต้องพิสูจน์สิทธิ์","#b45309") if c["special"] else badge("ทั่วไป","#64748b")
            hc="#b91c1c" if c["top_cashier"]=="Take Order" else "#374151"
            flag="background:#fdf8ee;" if c["special"] else ""
            add(f'<tr style="{flag}"><td style="font-size:11px">{c["name"][:34]}</td><td class="num">{c["bills"]}</td><td class="num">{c["covers"]:,}</td><td class="num" style="font-weight:700">{cpb}</td><td class="num">{fmt(c["amt"])}</td><td style="font-size:11px;color:{hc};font-weight:700"><code>{c["top_cashier"]}</code> ({c["top_n"]})</td><td class="center">{sp}</td></tr>')
        add('</tbody></table>')
    add(f"""<div class="alert alert-am">
<strong>ธงแดงเชิงความสมเหตุสมผล (ต้อง vouch):</strong> คูปอง "ผู้สูงอายุ 81 ปีขึ้นไป" (#35) ใช้กับ <strong>{ub_eld['covers']:,} ที่นั่ง</strong> ใน {ub_eld['bills']} บิล
= เฉลี่ย {round(ub_eld['covers']/max(ub_eld['bills'],1),1)} ที่นั่ง/บิล — แปลว่า<u>ทั้งโต๊ะ</u>ได้สิทธิ์ผู้สูงอายุ 81+ ไม่ใช่เฉพาะผู้สูงอายุ
อายุ 81+ เป็นเกณฑ์ที่สูงมาก ปริมาณระดับนี้ต้องทดสอบกับหลักฐานบัตรประชาชน/อายุจริง</div>
<div class="alert alert-blue">
<strong>ข้อสังเกตเชิงวิชาชีพ:</strong> การที่ <code>Take Order</code> เป็นผู้กดคูปองสิทธิ์สูง <u>พบที่สาขา master อื่นด้วย</u> (โคราช1, เชียงใหม่) จึง<strong>อาจเป็น workflow ปกติ</strong>
(พนักงานรับออเดอร์เลือกคูปองตอนรับออเดอร์) — ไม่ใช่หลักฐานทุจริตในตัวเอง แต่เป็น <u>control weakness</u> ที่ควรจำกัดสิทธิ์ + ตั้ง approval</div>
<div class="two-col">
<div>
<strong>DOW สาขาอุบลฯ</strong><div style="height:8px">""")
    max_ud_dow=max(r[2] for r in ud["by_dow"]) if ud["by_dow"] else 1
    for dow,n,disc in ud["by_dow"]:
        c="#dc2626" if dow in ("อา.","ส.") else "#ea580c"
        add(bar_row(dow,disc,max_ud_dow,c))
    add('</div></div><div><strong>Hour Distribution อุบลฯ</strong><div style="height:8px">')
    max_ud_hr=max(r[2] for r in ud["by_hour"]) if ud["by_hour"] else 1
    for hr,n,disc in ud["by_hour"]:
        c="#dc2626" if hr in ("19","20","18") else "#ea580c"
        add(bar_row(f"{hr}:00",disc,max_ud_hr,c))
    add('</div></div></div>')
    add('</div></div>')

    # ── UBON DEEP DIVE 3: Big Bills ───────────────────────────────────────────
    add(page_hdr("อุบลฯ — บิลส่วนลดสูง & เปรียบเทียบรายเดือน"))
    add('<div class="two-col"><div><strong>Top 15 บิลส่วนลดสูงสุด</strong>')
    add('<table style="margin-top:8px"><thead><tr><th>วันที่</th><th>เวลา</th><th>User</th><th class="num">ส่วนลด</th><th class="num">%ลด</th><th>คน</th></tr></thead><tbody>')
    for d,t,u,disc,base,pct,cust in ud["top_bills"][:15]:
        c=ubon_colors.get(u,"#374151")
        add(f'<tr><td style="font-size:11px">{d}</td><td style="font-size:11px">{t}</td><td style="color:{c};font-weight:700;font-size:11px"><code>{u}</code></td><td class="num" style="color:{c};font-weight:700">{fmt(disc)}</td><td class="num">{pct}%</td><td class="num">{cust or "-"}</td></tr>')
    add('</tbody></table></div><div>')
    add('<strong>Fixed Amount Patterns</strong>')
    add('<table style="margin-top:8px"><thead><tr><th class="num">฿ ส่วนลด</th><th class="num">ครั้ง</th><th class="num">รวม</th></tr></thead><tbody>')
    for amt,n in ud["fixed_amounts"]:
        add(f'<tr><td class="num" style="font-family:monospace;font-weight:700">฿{amt:,.0f}</td><td class="num">{n}</td><td class="num">{fmt(amt*n)}</td></tr>')
    add('</tbody></table></div></div>')
    add('</div></div>')

    # ── PART 4 — APPENDIX: methodology, confound, per-person detail ────────────
    if robust:
        _add_methodology_pages(add, robust, chain)
        _add_perperson_overview(add, robust)

    # ── PER-PERSON PAGES (appendix detail) ────────────────────────────────────
    for c in corr:
        branch_name="สุรินทร์" if c["branch"]=="surin" else "อุบลราชธานี"
        bdata=db_data[c["branch"]]
        colors=surin_user_colors if c["branch"]=="surin" else ubon_colors
        lc=lift_color(c["lift"])
        rb=rb_people.get(c["eid"])  # robust per-person record

        # risk level now driven by the ROBUST verdict, not the confounded raw lift
        if rb:
            if "ควรสืบเพิ่ม" in rb["verdict"]:
                risk_level="🔴 ควรสืบเพิ่ม"; risk_color="#dc2626"
            elif "confounded" in rb["verdict"]:
                risk_level="🟡 ปนตารางกะ"; risk_color="#d97706"
            else:
                risk_level="🟢 ไม่มีสัญญาณ"; risk_color="#16a34a"
            hero_lift=rb["pct_lift"]; hero_lc=lift_color(hero_lift or 1)
        else:
            risk_level="🟢 ต่ำ"; risk_color="#16a34a"; hero_lift=c["lift"]; hero_lc=lc

        # Page 1 of person: header, stats, POS account analysis
        add(page_hdr(f"วิเคราะห์รายบุคคล — {c['nick']} ({c['fullname'][:20]}) · {branch_name}"))
        add(f"""<div class="person-hdr">
<div>
<div class="person-name">{c['nick']} ({c['fullname']})</div>
<div class="person-role">{c['role']} · สาขา{branch_name}</div>
<div class="person-eid">รหัสพนักงาน: {c['eid']}</div>
</div>
<div style="text-align:right">
<div style="font-size:28px;font-weight:800;color:#fbbf24">{hero_lift}x</div>
<div style="font-size:12px;opacity:.8">Lift ปรับ disc% (ดิบ {c['lift']}x = ลวง)</div>
<div style="margin-top:6px">{badge(risk_level, risk_color)}</div>
</div>
</div>""")
        add(f"""<div class="person-stats">
<div class="pstat"><div class="pstat-val">{c['worked_days']}</div><div class="pstat-lbl">วันทำงาน</div></div>
<div class="pstat"><div class="pstat-val">{c['off_days']}</div><div class="pstat-lbl">วันหยุด</div></div>
<div class="pstat"><div class="pstat-val" style="color:{lc}">{fmt(c['worked_disc_mean'])}</div><div class="pstat-lbl">เฉลี่ยส่วนลด/วันทำงาน</div></div>
<div class="pstat"><div class="pstat-val" style="color:#16a34a">{fmt(c['off_disc_mean'])}</div><div class="pstat-lbl">เฉลี่ยส่วนลด/วันหยุด</div></div>
<div class="pstat"><div class="pstat-val" style="color:{lc}">{c['lift']}x</div><div class="pstat-lbl">Lift ratio</div></div>
</div>""")
        # Statistical box — RAW (confounded) vs ADJUSTED (revenue-normalized)
        if rb:
            skew=rb["skew"]; excl=rb["exclusive"]
            dom_dow=DOW_TH_FULL[skew["dominant_off_dow"]] if skew["dominant_off_dow"] is not None else "—"
            bonf_txt=(badge("✓ ผ่าน p<"+f"{bonf['threshold']:.4f}","#dc2626") if rb["bonferroni_pass"]
                      else badge("✗ ตก (ไม่นัยสำคัญ)","#16a34a"))
            add(f"""<div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:14px">
<div style="background:#fef2f2;border:1px solid #fca5a5;border-radius:8px;padding:14px">
<div style="font-weight:700;margin-bottom:8px;color:#991b1b">วิธีดิบ (ยอดส่วนลด — ปนยอดขาย, ลวง)</div>
<table style="font-size:12px;margin:0"><tbody>
<tr><td style="color:#64748b;padding:4px 0">เฉลี่ยส่วนลด/วันทำงาน</td><td style="font-weight:700;text-align:right">{fmt(rb['mean_raw_worked'])}</td></tr>
<tr><td style="color:#64748b;padding:4px 0">เฉลี่ยส่วนลด/วันหยุด</td><td style="font-weight:700;text-align:right">{fmt(rb['mean_raw_off'])}</td></tr>
<tr><td style="color:#64748b;padding:4px 0">Lift ดิบ</td><td style="font-weight:800;text-align:right;color:#dc2626">{rb['raw_lift']}x</td></tr>
<tr><td style="color:#64748b;padding:4px 0">p (permutation)</td><td style="text-align:right">{p_badge(rb['p_raw'])}</td></tr>
</tbody></table>
</div>
<div style="background:#f0fdf4;border:1px solid #86efac;border-radius:8px;padding:14px">
<div style="font-weight:700;margin-bottom:8px;color:#14532d">วิธีปรับ (disc% — ควบคุมยอดขาย)</div>
<table style="font-size:12px;margin:0"><tbody>
<tr><td style="color:#64748b;padding:4px 0">disc% เฉลี่ยวันทำงาน</td><td style="font-weight:700;text-align:right">{rb['mean_pct_worked']}%</td></tr>
<tr><td style="color:#64748b;padding:4px 0">disc% เฉลี่ยวันหยุด</td><td style="font-weight:700;text-align:right">{rb['mean_pct_off']}%</td></tr>
<tr><td style="color:#64748b;padding:4px 0">Lift ปรับ</td><td style="font-weight:800;text-align:right;color:{hero_lc}">{rb['pct_lift']}x</td></tr>
<tr><td style="color:#64748b;padding:4px 0">p (permutation) หลัง Bonferroni</td><td style="text-align:right">{bonf_txt}</td></tr>
</tbody></table>
</div>
</div>
<div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px;margin-bottom:14px">
<div class="pstat"><div class="pstat-val" style="font-size:15px">{dom_dow} ({skew['dominant_off_n']})</div><div class="pstat-lbl">วันหยุดหลัก (DOW skew)</div></div>
<div class="pstat"><div class="pstat-val" style="font-size:15px">{int(skew['weekend_off_share']*100)}%</div><div class="pstat-lbl">วันหยุดที่ตกเสาร์-อาทิตย์</div></div>
<div class="pstat"><div class="pstat-val" style="font-size:15px;color:{'#dc2626' if excl['n']==0 else '#16a34a'}">{excl['n']}</div><div class="pstat-lbl">วันทำงานเดี่ยว (แยกตัวได้?)</div></div>
</div>""")
        else:
            add("")

        # POS account analysis: on worked days vs off days
        acc_w=c["acc_worked"]
        acc_o=c["acc_off"]
        all_users=sorted(set(list(acc_w.keys())+list(acc_o.keys())),key=lambda u:-(acc_w.get(u,[0,0])[1]+acc_o.get(u,[0,0])[1]))
        add('<strong>POS Account ที่ใช้ในวันที่เข้างาน vs วันหยุด</strong>')
        add('<table style="margin-top:8px"><thead><tr><th>POS User</th><th class="num">วันทำงาน: จำนวนวัน</th><th class="num">วันทำงาน: ส่วนลดรวม</th><th class="num">วันหยุด: จำนวนวัน</th><th class="num">วันหยุด: ส่วนลดรวม</th></tr></thead><tbody>')
        for u in all_users[:8]:
            wd,ws=acc_w.get(u,[0,0])
            od,os_=acc_o.get(u,[0,0])
            col=colors.get(u,"#374151")
            add(f'<tr><td style="color:{col};font-weight:700"><code>{u}</code></td><td class="num">{wd} วัน</td><td class="num" style="color:{col};font-weight:700">{fmt(ws)}</td><td class="num">{od} วัน</td><td class="num">{fmt(os_)}</td></tr>')
        add('</tbody></table>')

        # Verdict — based on ROBUST analysis (revenue-adjusted + Bonferroni + confounds)
        if rb:
            reasons=rb["confound_reasons"]
            if "ควรสืบเพิ่ม" in rb["verdict"]:
                interp=f'<strong>{rb["verdict"]} (disc% lift {rb["pct_lift"]}x, p={rb["p_pct"]}):</strong> ผ่าน Bonferroni และยังหา confound อธิบายไม่ได้ — ควรตรวจ POS login log + CCTV เพิ่ม (ยัง<u>ไม่ใช่</u>หลักฐานชี้ตัว)'
            elif "confounded" in rb["verdict"]:
                rtxt=" · ".join(reasons) if reasons else "อธิบายได้ด้วยตารางกะ"
                interp=f'<strong>{rb["verdict"]} (disc% lift {rb["pct_lift"]}x, p={rb["p_pct"]}):</strong> แม้ผ่านเกณฑ์สถิติ แต่อธิบายได้ด้วย: {rtxt} — จึง<u>ไม่ใช่</u>หลักฐานพฤติกรรม'
            else:
                base=f'<strong>{rb["verdict"]} (disc% lift {rb["pct_lift"]}x, p={rb["p_pct"]}):</strong> หลังควบคุมยอดขาย + แก้ multiple-comparison ความสัมพันธ์ไม่มีนัยสำคัญ'
                base+= f' (Lift ดิบ {rb["raw_lift"]}x เกิดจาก revenue/DOW confound)' if (rb["raw_lift"] or 0)>=1.3 else ''
                interp=base
            if rb["exclusive"]["n"]==0:
                interp+=' <span style="color:#dc2626">· วันทำงานเดี่ยว=0 → แยกตัวบุคคลไม่ได้</span>'
        else:
            interp='ไม่มีข้อมูลเพียงพอ'
        bgmap={"#dc2626":"#fef2f2","#ea580c":"#fff7ed","#d97706":"#fffbeb","#16a34a":"#f0fdf4"}
        add(f'<div class="verdict-box" style="border-color:{risk_color};background:{bgmap.get(risk_color,"#f0fdf4")}"><strong style="color:{risk_color}">{risk_level}</strong><br><span style="font-size:12px">{interp}</span></div>')
        add('</div></div>')

        # Page 2 of person: calendar view + top days
        add(page_hdr(f"{c['nick']} — ปฏิทินเข้างาน & วันส่วนลดสูง"))
        add('<div class="sec-label">ATTENDANCE CALENDAR</div>')
        add('<div class="sec-title" style="font-size:15px">ปฏิทินเข้างาน + ระดับส่วนลดแต่ละวัน</div>')
        add("""<div style="display:flex;gap:16px;align-items:center;margin-bottom:10px;font-size:11px">
<span style="display:flex;align-items:center;gap:4px"><span style="width:16px;height:16px;border:2px solid #1e3a5f;border-radius:2px;display:inline-block;background:#fee2e2"></span>วันทำงาน (กรอบน้ำเงิน)</span>
<span style="display:flex;align-items:center;gap:4px"><span style="width:16px;height:16px;border:1px dashed #cbd5e1;border-radius:2px;display:inline-block;background:#f0f4f8"></span>วันหยุด</span>
<span style="display:flex;align-items:center;gap:4px"><span style="width:12px;height:12px;background:#fee2e2;border-radius:2px;display:inline-block"></span>ส่วนลด≥5K</span>
<span style="display:flex;align-items:center;gap:4px"><span style="width:12px;height:12px;background:#ffedd5;border-radius:2px;display:inline-block"></span>2K-5K</span>
<span style="display:flex;align-items:center;gap:4px"><span style="width:12px;height:12px;background:#fef9c3;border-radius:2px;display:inline-block"></span>500-2K</span>
<span style="display:flex;align-items:center;gap:4px"><span style="width:12px;height:12px;background:#dcfce7;border-radius:2px;display:inline-block"></span>&lt;500</span>
</div>""")
        add('<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:12px">')
        for mo in ["2026-01","2026-02","2026-03","2026-04","2026-05"]:
            add(calendar_month(mo, c["att"], bdata["daily"]))
        add('</div>')

        # Top days with high discounts when working
        add('<strong style="display:block;margin-top:16px">Top 10 วันส่วนลดสูงสุด ขณะเข้างาน</strong>')
        add('<table style="margin-top:6px"><thead><tr><th>วันที่</th><th class="num">ส่วนลดรวมสาขา</th><th class="num">บิล</th><th>User หลัก</th><th class="num">ส่วนลด/บิล เฉลี่ย</th></tr></thead><tbody>')
        for d,disc,users_on_day in c["top_days"]:
            top_u=sorted(users_on_day.items(),key=lambda x:-x[1]["disc"])
            top_u_str=", ".join(f'<code style="color:{colors.get(u,"#374151")}">{u}</code> {fmt(v["disc"])}' for u,v in top_u[:3])
            daily_n=bdata["daily"].get(d,{}).get("n",1) or 1
            add(f'<tr><td>{d}</td><td class="num" style="font-weight:700;color:#dc2626">{fmt(disc)}</td><td class="num">{daily_n}</td><td style="font-size:11px">{top_u_str}</td><td class="num">{fmt(disc/daily_n)}</td></tr>')
        add('</tbody></table>')

        if c["off_days_list"]:
            add(f'<div class="alert alert-am" style="margin-top:8px"><strong>วันหยุดที่มีส่วนลดสูง (Top {len(c["off_days_list"])} วัน):</strong> ')
            off_detail=[]
            for d in sorted(c["off_days_list"],key=lambda d:-bdata["daily"].get(d,{}).get("disc",0))[:8]:
                disc=bdata["daily"].get(d,{}).get("disc",0)
                off_detail.append(f'{d} ({fmt(disc)})')
            add(", ".join(off_detail))
            add('</div>')
        add('</div></div>')

    # ── SURIN COMPARISON TABLE ────────────────────────────────────────────────
    add(page_hdr("สรุปเปรียบเทียบ — สุรินทร์ 4 คน"))
    add('<div class="sec-label">SURIN SUMMARY</div><div class="sec-title">เปรียบเทียบพนักงาน 4 คน สาขาสุรินทร์</div>')
    _build_summary_table(parts, surin_corr, surin_user_colors, db_data["surin"])
    add('</div></div>')

    # ── UBON COMPARISON TABLE ─────────────────────────────────────────────────
    add(page_hdr("สรุปเปรียบเทียบ — อุบลฯ 5 คน"))
    add('<div class="sec-label">UBON SUMMARY</div><div class="sec-title">เปรียบเทียบพนักงาน 5 คน สาขาอุบลฯ</div>')
    _build_summary_table(parts, ubon_corr, ubon_colors, db_data["ubon"])
    add('</div></div>')

    # ── CORRELATION FULL TABLE ────────────────────────────────────────────────
    add(page_hdr("ภาคผนวก: Correlation ดิบ (เพื่ออ้างอิงเปรียบเทียบ)"))
    add("""<div class="sec-label">APPENDIX · RAW (UNADJUSTED) CORRELATION</div>
<div class="sec-title">Welch's t-test + Pearson — ฉบับดิบ ไม่ควบคุมตัวแปรกวน</div>
<div class="alert alert-red">
<strong>⚠ คำเตือน:</strong> ตารางนี้คือการวิเคราะห์ <u>ดิบ</u> (ยอดส่วนลด ไม่หารยอดขาย ไม่แก้ multiple-comparison)
เก็บไว้<strong>เพื่อเปรียบเทียบให้เห็นว่ามันให้ภาพลวงอย่างไร</strong> — Lift ที่ดูสูงในตารางนี้เกิดจาก revenue/DOW confound
<strong>ข้อสรุปที่ใช้จริงอยู่ที่หน้า "ผลรายบุคคล (ปรับแก้แล้ว)" และหน้าระเบียบวิธี</strong> ไม่ใช่หน้านี้
</div>
<div class="alert alert-blue">
<strong>วิธีการ (ดิบ):</strong> เปรียบเทียบยอดส่วนลดรวมของสาขาในวันที่พนักงานเข้างาน vs วันที่ไม่เข้างาน
<strong>Welch's t-test</strong>, <strong>Pearson r</strong>, <strong>Lift</strong> = เฉลี่ยวันทำงาน ÷ เฉลี่ยวันหยุด</div>""")
    add('<table><thead><tr><th>ชื่อ</th><th>ตำแหน่ง</th><th>สาขา</th><th class="num">วันทำงาน</th><th class="num">วันหยุด</th><th class="num">เฉลี่ยทำงาน</th><th class="num">เฉลี่ยหยุด</th><th class="num">Lift</th><th class="num">t</th><th class="center">p</th><th class="num">r</th></tr></thead><tbody>')
    for c in sorted(corr,key=lambda x:-x["lift"]):
        lc=lift_color(c["lift"])
        bg="background:#fef2f2;" if c["lift"]>=1.5 and c["p"]<0.1 else ""
        bn="สุรินทร์" if c["branch"]=="surin" else "อุบลฯ"
        add(f'<tr style="{bg}"><td><strong>{c["nick"]}</strong><br><span style="font-size:10px;color:#94a3b8">{c["fullname"][:16]}</span></td><td style="font-size:11px">{c["role"]}</td><td style="font-size:11px">{bn}</td><td class="num">{c["worked_days"]}</td><td class="num">{c["off_days"]}</td><td class="num" style="color:{lc};font-weight:700">{fmt(c["worked_disc_mean"])}</td><td class="num">{fmt(c["off_disc_mean"])}</td><td class="num" style="color:{lc};font-weight:800">{c["lift"]}x</td><td class="num">{c["t"]}</td><td class="center">{p_badge(c["p"])}</td><td class="num">{c["r"]}</td></tr>')
    add('</tbody></table>')
    add('</div></div>')

    # ── OFF-DAY ANALYSIS ──────────────────────────────────────────────────────
    add(page_hdr("Off-Day Analysis — ส่วนลดในวันที่พนักงานไม่ได้เข้างาน"))
    add("""<div class="sec-label">OFF-DAY ANALYSIS</div>
<div class="sec-title">ส่วนลดวันหยุดพนักงาน</div>
<div class="sec-desc">ถ้าวันที่พนักงานหยุดงานยังมีส่วนลดสูง → อาจมีผู้สมรู้ร่วมคิดหรือ account ถูก share</div>""")
    add('<table><thead><tr><th>ชื่อ</th><th>ตำแหน่ง</th><th>สาขา</th><th class="num">วันหยุด</th><th class="num">มีส่วนลด</th><th class="num">%</th><th class="num">ส่วนลดรวมวันหยุด</th><th class="center">ความเสี่ยง</th></tr></thead><tbody>')
    for c in sorted(corr,key=lambda x:-x["off_disc_total"]):
        off_pct=round(100*c["off_days_with_disc"]/max(c["off_days"],1))
        bn="สุรินทร์" if c["branch"]=="surin" else "อุบลฯ"
        if off_pct>=70: rc,rl="#dc2626","🔴 สูงมาก"
        elif off_pct>=50: rc,rl="#ea580c","🟠 สูง"
        elif off_pct>=30: rc,rl="#d97706","🟡 ปานกลาง"
        else: rc,rl="#16a34a","🟢 ต่ำ"
        add(f'<tr><td><strong>{c["nick"]}</strong><br><span style="font-size:10px;color:#94a3b8">{c["fullname"][:16]}</span></td><td style="font-size:11px">{c["role"]}</td><td style="font-size:11px">{bn}</td><td class="num">{c["off_days"]}</td><td class="num">{c["off_days_with_disc"]}</td><td class="num" style="color:{rc};font-weight:700">{off_pct}%</td><td class="num">{fmt(c["off_disc_total"])}</td><td class="center">{badge(rl,rc)}</td></tr>')
    add('</tbody></table>')
    add("""<div class="alert alert-am">
<strong>การตีความ:</strong> วันที่พนักงานหยุดแต่มีส่วนลดสูง อาจหมายความว่า:
(1) คนอื่นใช้ account ของพนักงานแทน · (2) ส่วนลดเกิดขึ้นโดยไม่เกี่ยวกับบุคคลนั้น · (3) พนักงานมาทำงานจริงแต่ไม่ได้เช็คอิน
ข้อ (1) เป็นสัญญาณร้ายแรง ควรตรวจ CCTV + POS login log ในวันที่น่าสงสัย</div>""")
    add('</div></div>')

    # ── FINANCIAL EXPOSURE vs POLICY CEILING ──────────────────────────────────
    tot_exp=sd_exp+ud_exp+udon_exp
    add(page_hdr("ประมาณการส่วนลดส่วนเกิน (เทียบเพดานนโยบาย)"))
    add(f"""<div class="sec-label">Excess discount vs policy ceiling</div>
<div class="sec-title">ส่วนเกินจากเพดาน ≤{POLICY}% — ทั้ง 3 สาขา master เกินหมด</div>
<div class="alert alert-am">
<strong>เกณฑ์:</strong> นโยบาย ส่วนลด<u>ไม่ควรเกิน {POLICY_STRICT}-{POLICY}%</u> ของยอดขาย · ตารางใช้เพดาน {POLICY}% (อนุรักษ์นิยม) · คอลัมน์ {POLICY_STRICT}% = เข้มกว่า
"ส่วนเกิน" = ส่วนลดที่เกินเพดาน — เป็น<u>ความเสี่ยง/รูรั่ว</u>ที่ต้องอธิบาย ไม่ได้แปลว่ายักยอกทั้งหมด (ส่วนหนึ่งอาจเป็นสิทธิ์จริง แต่ก็สะท้อนการคุมส่วนลดหละหลวม)</div>
<table><thead><tr><th>สาขา</th><th class="num">ยอดขาย</th><th class="num">% ส่วนลด</th>
<th class="num">ส่วนเกิน vs {POLICY}%</th><th class="num">ส่วนเกิน vs {POLICY_STRICT}%</th></tr></thead><tbody>
<tr style="background:#fdf2f2"><td><strong>อุดรธานี</strong> (จับได้)</td><td class="num">{fmt(udon_rev)}</td><td class="num" style="color:var(--rd);font-weight:800">{udon_pct}%</td><td class="num" style="color:var(--rd);font-weight:700">{fmt(udon_exp)}</td><td class="num" style="color:var(--rd)">{fmt(max(0,round((udon_pct-POLICY_STRICT)/100*udon_rev)))}</td></tr>
<tr style="background:#fdf2f2"><td><strong>สุรินทร์</strong></td><td class="num">{fmt(sd['total_rev'])}</td><td class="num" style="color:var(--rd);font-weight:700">{sd['disc_pct']}%</td><td class="num" style="color:var(--rd);font-weight:700">{fmt(sd_exp)}</td><td class="num" style="color:var(--rd)">{fmt(max(0,round((sd['disc_pct']-POLICY_STRICT)/100*sd['total_rev'])))}</td></tr>
<tr style="background:#fdf2f2"><td><strong>อุบลราชธานี</strong></td><td class="num">{fmt(ud['total_rev'])}</td><td class="num" style="color:var(--rd);font-weight:700">{ud['disc_pct']}%</td><td class="num" style="color:var(--rd);font-weight:700">{fmt(ud_exp)}</td><td class="num" style="color:var(--rd)">{fmt(max(0,round((ud['disc_pct']-POLICY_STRICT)/100*ud['total_rev'])))}</td></tr>
<tr style="border-top:2px solid var(--rd)"><td><strong>รวม 3 สาขา</strong></td><td class="num">{fmt(sd['total_rev']+ud['total_rev']+udon_rev)}</td><td class="num">—</td><td class="num" style="color:var(--rd);font-weight:800;font-size:14px">{fmt(tot_exp)}</td><td class="num" style="color:var(--rd);font-weight:800">{fmt(max(0,round((udon_pct-POLICY_STRICT)/100*udon_rev))+max(0,round((sd['disc_pct']-POLICY_STRICT)/100*sd['total_rev']))+max(0,round((ud['disc_pct']-POLICY_STRICT)/100*ud['total_rev'])))}</td></tr>
</tbody></table>
<div class="kpi3">
<div class="kpi"><div class="kpi-val red">{fmt(tot_exp)}</div><div class="kpi-lbl">ส่วนเกินเพดาน {POLICY}% รวม 3 สาขา (5 เดือน)</div></div>
<div class="kpi"><div class="kpi-val red">{fmt(round(tot_exp*12/5))}</div><div class="kpi-lbl">ประมาณการ 12 เดือน</div></div>
<div class="kpi"><div class="kpi-val am">{master_pct}%</div><div class="kpi-lbl">ค่าเฉลี่ย master — เกินเพดานทั้งกลุ่ม</div></div>
</div>
<div class="alert alert-red"><strong>ข้อสรุป:</strong> ไม่มีสาขา master ใดอยู่ในเพดานนโยบาย — แม้แต่อุบลฯ ({ud['disc_pct']}%) ก็เกิน ฿{ud_exp:,.0f}
การที่ค่าเฉลี่ย master ({master_pct}%) สูงกว่าเพดาน แสดงว่าเป็น<u>ปัญหาเชิงระบบของกลุ่ม master</u> ไม่ใช่แค่ราย outlier ·
<strong>เทียบบรรทัดฐานที่ถูก = เพดานนโยบาย ไม่ใช่ค่าเฉลี่ยของกลุ่มที่ก็ผิดเกณฑ์เหมือนกัน</strong> (franchise {franch_pct}% = ตัวอย่างที่อยู่ในเพดานเพราะจ่ายเงินตัวเอง)</div>""")
    add('</div></div>')

    # ── AUDIT TESTS / PROCEDURES ──────────────────────────────────────────────
    add(page_hdr("ขั้นตอนการตรวจสอบที่แนะนำ (Audit Procedures)"))
    add(f"""<div class="sec-label">Recommended audit procedures</div>
<div class="sec-title" style="font-size:16px">การทดสอบเพื่อยืนยันข้อค้นพบก่อนสรุปความผิด</div>
<div class="alert alert-blue">วิธีปฏิบัติตามมาตรฐานตรวจสอบ: แต่ละข้อค้นพบ (finding) จับคู่กับ <strong>การทดสอบ (test)</strong> และ <strong>หลักฐานที่ต้องได้ (evidence)</strong> ·
ผลวิเคราะห์ข้อมูล (analytical) เป็นเพียง <em>risk indicator</em> ต้อง vouch กับเอกสารต้นทางก่อนสรุป</div>
<table>
<thead><tr><th style="width:17%">สาขา/ข้อค้นพบ</th><th style="width:30%">การทดสอบ (audit test)</th><th style="width:28%">หลักฐานที่ต้องได้</th><th style="width:25%">ผลที่คาดหวัง / เกณฑ์</th></tr></thead>
<tbody>
<tr style="background:#fdf2f2"><td><strong>อุดร</strong> — คูปองเด็ก #33 ยักเงินสด</td>
<td>สุ่มบิลคูปองเด็ก #33 ที่จ่ายเงินสด เทียบ timestamp การกดคูปอง vs เวลาปิดบิล + กระทบยอดเงินสดลิ้นชัก (cash-count)</td>
<td>POS audit log (เวลากดคูปอง), pos_cashcount_day, CCTV ช่วงเวลานั้น</td>
<td>คูปองถูกกด<u>หลัง</u>ปิดบิล + เงินสดขาด = ยืนยันการยักยอก</td></tr>
<tr style="background:#fdf8ee"><td><strong>อุบล</strong> — คูปองผู้สูงอายุ81+ (#35) ครอบทั้งโต๊ะ</td>
<td>สุ่มบิล #35 (covers/บิล สูง) ขอหลักฐานอายุของผู้รับสิทธิ์ทุกที่นั่งในบิล · วิเคราะห์ covers ที่ได้สิทธิ์ vs ขนาดโต๊ะ</td>
<td>สำเนาบัตร ปชช./หลักฐานอายุ, นโยบายเงื่อนไขคูปอง, ภาพถ่ายโต๊ะ (ถ้ามี)</td>
<td>จำนวนผู้มีสิทธิ์จริง &lt; ที่นั่งที่ได้ส่วนลด = ใช้สิทธิ์เกิน/ปลอม</td></tr>
<tr style="background:#fdf8ee"><td><strong>อุบล</strong> — คูปองผู้ถือหุ้น/ผู้บริหาร</td>
<td>กระทบรายชื่อผู้รับสิทธิ์กับทะเบียนผู้ถือหุ้น/ผู้บริหารจริง</td>
<td>ทะเบียนผู้ถือหุ้น, รายชื่อผู้บริหารที่ได้สิทธิ์</td>
<td>ผู้รับสิทธิ์ไม่อยู่ในทะเบียน = comp ไม่มีสิทธิ์</td></tr>
<tr style="background:#fdf2f2"><td><strong>สุรินทร์</strong> — comp ไม่มีเอกสาร {sd_neither_pct}%</td>
<td>สุ่ม comp ที่ไม่ผูกคูปอง/เหตุผล (admin+Manager) ขอเอกสารอนุมัติ/เหตุผลประกอบรายบิล + สัมภาษณ์ผู้จัดการ</td>
<td>ใบอนุมัติส่วนลด, บันทึกเหตุผล, POS login log (ใครคุม account)</td>
<td>ไม่มีเอกสารรองรับ = ละเมิดนโยบาย/อาจเป็น comp พวกพ้อง</td></tr>
<tr><td><strong>ทุกสาขา</strong> — การแชร์บัญชี POS</td>
<td>ดึง POS login log เทียบเวลา login จริง กับ shift/ตารางกะ และเวลาบิลส่วนลด</td>
<td>POS login/session log จาก Easy, ตารางกะ</td>
<td>1 account ถูกใช้โดยหลายคน/นอกกะ = แชร์ credential</td></tr>
<tr><td><strong>ทุกสาขา</strong> — ช่องยกเลิกบิล (void/cancel)</td>
<td>ตรวจ pos_cancel_bill_detail: บิลที่ยกเลิก<u>หลัง</u>ชำระ + มี cancel_user ซ้ำผิดปกติ</td>
<td>pos_cancel_bill_detail (cancel_user), ใบกำกับ/สลิป</td>
<td>ยกเลิกหลังรับเงิน = ช่องยักเงินสดอีกทาง</td></tr>
</tbody></table>
<div class="alert alert-am"><strong>คำรับรองจากผู้บริหาร (Management Representations) ที่ต้องทดสอบอิสระ:</strong>
(1) "อุดรพิสูจน์ทุจริตแล้ว" — สอบทานข้อมูลแล้วพบ pattern ตรง (คูปองเด็ก #33 ผ่าน admin/เงินสด) · ควรได้สำนวน/บันทึกการสอบสวนประกอบแฟ้ม
(2) "ส่วนลดพิเศษมาจาก Take Order เป็นส่วนใหญ่" — ยืนยันตรงกับข้อมูล (แต่พบที่ master อื่นด้วย = อาจเป็น workflow)
(3) นโยบายสิทธิ์การให้คูปอง/ส่วนลดแต่ละ account — ขอเอกสารนโยบายเพื่อประเมิน segregation of duties</div>
<div class="alert alert-blue"><strong>ขอบเขต (scope) & ข้อจำกัด:</strong> ใช้ข้อมูล POS ม.ค.–พ.ค. 2569 (ช่อง discount + coupon_series; sp_discount=0) + ตารางกะ 9 คน ·
<u>ไม่ครอบคลุม</u>: การ map บัญชี POS→ตัวบุคคล (ไม่มีในระบบ), หลักฐานต้นทางนอกระบบ (บัตร/ใบอนุมัติ/CCTV), ช่วงก่อน ม.ค. 2569 ·
ข้อสรุประดับ "สาขา/ระบบควบคุม" เชื่อถือได้ · ระดับ "ตัวบุคคล" ต้องมีหลักฐานเพิ่มตามตารางข้างต้น</div>""")
    add('</div></div>')

    # ── RED FLAGS & ACTION PLAN ───────────────────────────────────────────────
    add(page_hdr("Red Flags & Action Plan"))
    sd_sig=robust['branches']['surin']['cash_signature']['lift'] if robust else 0
    ud_sig=robust['branches']['ubon']['cash_signature']['lift'] if robust else 0
    add(f"""<div class="flag-grid">
<div class="flag flag-red"><div style="font-weight:700;color:#dc2626">🔴 RF-01 [วิกฤต] สุรินทร์: comp ไม่มีเอกสาร {sd_neither_pct}%</div><div style="font-size:11px;margin-top:4px">{sd_att.get('neither_n',0):,} บิล ฿{sd_att.get('neither_amt',0):,.0f} ไม่ผูกคูปอง/เหตุผล ผ่าน admin+Manager (อุบล/อุดร=มี trail)</div></div>
<div class="flag flag-red"><div style="font-weight:700;color:#dc2626">🔴 RF-02 [วิกฤต] สุรินทร์: Manager/admin สลับวัน (co-occur 7%)</div><div style="font-size:11px;margin-top:4px">2 account ระดับสูงแทบไม่เคยทำงานวันเดียวกัน — แชร์รหัส/credential (red flag เด่นสุด)</div></div>
<div class="flag flag-red"><div style="font-weight:700;color:#dc2626">🔴 RF-03 [วิกฤต] อุบลฯ: Take Order ให้ส่วนลด {fmt(ud['by_user'][0][2])}</div><div style="font-size:11px;margin-top:4px">ไม่ควรมีสิทธิ์ — ปัญหา permission ทั้งเครือ (8 สาขา) ต้องแก้ทันที</div></div>
<div class="flag flag-am"><div style="font-weight:700;color:#d97706">🟡 RF-04 [สูง] ส่วนลดพิเศษไม่มีบันทึกประเภท</div><div style="font-size:11px;margin-top:4px">ผู้บริหาร/ผู้ถือหุ้น/ผู้สูงอายุ/เด็ก แยกจาก "ไม่มีเหตุผล" ไม่ได้ — ช่องโหว่ตรวจสอบ</div></div>
<div class="flag flag-red"><div style="font-weight:700;color:#dc2626">🔴 RF-05 [วิกฤต] ทั้งกลุ่ม master เกินเพดานนโยบาย ≤{POLICY}%</div><div style="font-size:11px;margin-top:4px">เฉลี่ย master {robust['peer']['by_type']['master']['pct'] if robust else '?'}% (franchise {robust['peer']['by_type']['franchise']['pct'] if robust else '?'}%) — over-discounting เชิงระบบ ทุกสาขา master เกินเพดาน</div></div>
<div class="flag flag-am"><div style="font-weight:700;color:#d97706">🟢 RF-06 [เฝ้าระวัง] cash-signature Surin/Ubon = ปกติ</div><div style="font-size:11px;margin-top:4px">cash-lift {sd_sig}x/{ud_sig}x (อุดร 1.87x) — ไม่เข้าข่าย cash-pocketing แบบอุดร</div></div>
<div class="flag flag-red"><div style="font-weight:700;color:#dc2626">🔴 RF-07 [วิกฤต-อ้างอิง] อุดรธานี: cash-pocketing จับได้แล้ว</div><div style="font-size:11px;margin-top:4px">disc% 7.32% สูงสุดเครือ, cash-lift 1.87x — ใช้เป็น template ตรวจสาขาอื่น</div></div>
<div class="flag flag-am"><div style="font-weight:700;color:#d97706">🟡 RF-08 [สูง] ระดับบุคคล = สรุปจากตารางกะไม่ได้</div><div style="font-size:11px;margin-top:4px">exclusive-day=0 + revenue/DOW confound + ไม่มี POS→พนักงาน map → ต้อง login log + CCTV</div></div>
</div>
<strong>Action Plan ลำดับเร่งด่วน</strong>
<table style="margin-top:8px"><thead><tr><th>#</th><th>การดำเนินการ</th><th class="center">เร่งด่วน</th><th>ผู้รับผิดชอบ</th></tr></thead><tbody>
<tr><td>1</td><td><strong>ระงับสิทธิ์ Take Order ให้ส่วนลด อุบลฯ ทันที</strong></td><td class="center">{badge("ทันที","#dc2626")}</td><td>IT</td></tr>
<tr><td>2</td><td><strong>เปลี่ยนรหัส POS ทุก account ทั้ง 2 สาขา</strong></td><td class="center">{badge("ทันที","#dc2626")}</td><td>IT</td></tr>
<tr><td>3</td><td><strong>เรียกสัมภาษณ์ ผู้จัดการ+หัวหน้าสุรินทร์</strong> ขอคำอธิบายส่วนลดทุกรายการ</td><td class="center">{badge("เร่งด่วนมาก","#dc2626")}</td><td>HR/เจ้าของ</td></tr>
<tr><td>4</td><td><strong>บังคับระบุเหตุผลก่อนกดส่วนลด</strong> ใน POS ทุกสาขา</td><td class="center">{badge("เร่งด่วนสูง","#ea580c")}</td><td>IT</td></tr>
<tr><td>5</td><td><strong>ตั้ง Supervisor Approval Workflow</strong>: ส่วนลด>500฿ ต้องมีหัวหน้า approve</td><td class="center">{badge("เร่งด่วนสูง","#ea580c")}</td><td>IT/ผู้บริหาร</td></tr>
<tr><td>6</td><td><strong>รัน cash-signature sweep ทุกสาขา</strong> (cash-lift เหมือนอุดร) — หา cash-pocketing สาขาอื่น</td><td class="center">{badge("เร่งด่วนมาก","#dc2626")}</td><td>Audit/IT</td></tr>
<tr><td>7</td><td><strong>Export POS login log</strong>: ใครล็อกอินด้วย Manager/admin/Take Order แต่ละวัน-เวลา (เชื่อม timestamp บิลส่วนลด)</td><td class="center">{badge("เร่งด่วนสูง","#ea580c")}</td><td>IT</td></tr>
<tr><td>8</td><td><strong>ตรวจ CCTV เฉพาะบิลเงินสด+ส่วนลด</strong> (signature อุดร) ไม่ใช่ทุกบิล</td><td class="center">{badge("เร่งด่วนสูง","#ea580c")}</td><td>ผู้บริหาร</td></tr>
<tr><td>9</td><td><strong>บังคับระบุประเภทส่วนลด</strong> (เด็ก/ผู้สูงอายุ/ผู้บริหาร) + post-settlement lock ใน POS</td><td class="center">{badge("เร่งด่วนสูง","#ea580c")}</td><td>IT</td></tr>
<tr><td>10</td><td><strong>ตั้ง Daily Alert</strong> ส่วนลด + cash-lift รายสาขา แจ้งเจ้าของ</td><td class="center">{badge("ระยะกลาง","#d97706")}</td><td>IT</td></tr>
</tbody></table>
<div class="alert alert-blue" style="margin-top:10px"><strong>ลำดับโฟกัสตามหลักฐาน:</strong> (1) อุดร = ทุจริตพิสูจน์แล้ว ดำเนินคดี/ขยายผล · (2) cash-sweep หาสาขาอื่นที่ signature เดียวกัน ·
(3) สุรินทร์/อุบลฯ = แก้ <u>การควบคุม</u> (account, permission, no-reason) ก่อน — ยังไม่มีหลักฐาน cash-pocketing · (4) ระดับบุคคลรอ login log + CCTV</div>""")
    add('</div></div>')

    # ── CLOSE ────────────────────────────────────────────────────────────────
    add(f'<div style="text-align:center;padding:20px;font-size:11px;color:#94a3b8">OSHINEI ENTERPRISE · Fraud Investigation Report · {now} · STRICTLY CONFIDENTIAL</div>')
    add('</body></html>')
    return "".join(parts)


def _build_summary_table(parts, corr_list, colors, bdata):
    def add(h): parts.append(h)
    add('<table><thead><tr><th>ชื่อ</th><th>ตำแหน่ง</th><th class="num">วันทำงาน</th><th class="num">เฉลี่ยทำงาน</th><th class="num">เฉลี่ยหยุด</th><th class="num">Lift</th><th class="num">t</th><th class="center">p</th><th class="num">r</th><th class="center">ความเสี่ยง</th></tr></thead><tbody>')
    for c in sorted(corr_list,key=lambda x:-x["lift"]):
        lc=lift_color(c["lift"])
        if c["lift"]>=2 and c["p"]<0.05: risk=badge("🔴 สูงมาก","#dc2626")
        elif c["lift"]>=1.5 and c["p"]<0.1: risk=badge("🟠 สูง","#ea580c")
        elif c["lift"]>=1.2: risk=badge("🟡 ปานกลาง","#d97706")
        else: risk=badge("🟢 ต่ำ","#16a34a")
        top_acc=sorted(c["acc_worked"].items(),key=lambda x:-x[1][1])[:2]
        acc_str=", ".join(f'<code style="color:{colors.get(u,"#374151")}">{u}</code>' for u,_ in top_acc)
        add(f'<tr><td><strong>{c["nick"]}</strong><br><span style="font-size:10px;color:#94a3b8">{c["fullname"][:20]}</span><br><span style="font-size:10px">{acc_str}</span></td><td style="font-size:11px">{c["role"]}</td><td class="num">{c["worked_days"]}</td><td class="num" style="color:{lc};font-weight:700">{c["worked_disc_mean"]:,}</td><td class="num">{c["off_disc_mean"]:,}</td><td class="num" style="color:{lc};font-weight:800">{c["lift"]}x</td><td class="num">{c["t"]}</td><td class="center">{p_badge(c["p"])}</td><td class="num">{c["r"]}</td><td class="center">{risk}</td></tr>')
    add('</tbody></table>')


DOW_TH_FULL=["จันทร์","อังคาร","พุธ","พฤหัส","ศุกร์","เสาร์","อาทิตย์"]

def _add_peer_benchmark_pages(add, robust, chain):
    """Peer-group benchmark (master vs franchise) + Udon proven-fraud reference +
    cash-signature discriminating test applied to Surin/Ubon."""
    peer=robust["peer"]; udon=robust["udon"]
    sb=robust["branches"]["surin"]; ub=robust["branches"]["ubon"]
    master_pct=peer["by_type"].get("master",{}).get("pct",0)
    franch_pct=peer["by_type"].get("franchise",{}).get("pct",0)
    masters=[b for b in peer["branches"] if b["branch_type"]=="master"]
    # attribution locals (also used by the 3-patterns page in this function)
    sd_att=sb["attribution"]
    sd_neither_pct=round(100*sd_att["neither_n"]/max(sd_att["disc_bills"],1))
    ub_eld=next((c for c in ub["coupons"] if c["sid"]==35),{"bills":0,"covers":0,"amt":0})
    # policy ceiling locals (Ubon excess over policy, for the read-out line)
    POLICY=2.5; POLICY_STRICT=2.25
    _ubb=next((b for b in peer["branches"] if b["branch_id"]==26),None)
    _ub_rev=round(_ubb["disc"]/(_ubb["pct"]/100)) if _ubb and _ubb["pct"] else 0
    ud_exp=max(0,round((_ubb["pct"]-POLICY)/100*_ub_rev)) if _ubb else 0

    # ── PAGE: Peer benchmark ─────────────────────────────────────────────────
    add(page_hdr("Benchmark ที่เป็นธรรม: Master vs Franchise"))
    add(f"""<div class="sec-label">PEER-GROUP BENCHMARK</div>
<div class="sec-title" style="font-size:18px">เพดานนโยบาย ≤{POLICY}% คือเกณฑ์ — peer เป็นแค่บริบท</div>
<div class="alert alert-red">
<strong>เกณฑ์ที่ถูกต้อง = เพดานนโยบาย ≤{POLICY_STRICT}-{POLICY}%</strong> (ไม่ใช่ค่าเฉลี่ยของกลุ่มใดกลุ่มหนึ่ง)
สาขา <code>master</code> เฉลี่ย <strong>{master_pct}%</strong> = <u>เกินเพดานทั้งกลุ่ม</u> · <code>franchise</code> เฉลี่ย <strong>{franch_pct}%</strong> = อยู่ในเพดาน (จ่ายเงินตัวเอง)
<br><strong>ระวัง 2 กับดัก:</strong> (1) เทียบ chain ({chain}%) → ถ่วงต่ำด้วย franchise ดูเกินจริง · (2) เทียบค่าเฉลี่ย master ({master_pct}%) เป็น "เกณฑ์ผ่าน" → ผิด เพราะ master เองก็เกินเพดาน = "ยอมรับเพราะคนอื่นก็ทำ"</div>
<div class="kpi-grid">
<div class="kpi"><div class="kpi-val em">≤{POLICY}%</div><div class="kpi-lbl">เพดานนโยบาย (เกณฑ์จริง)</div></div>
<div class="kpi"><div class="kpi-val em">{franch_pct}%</div><div class="kpi-lbl">franchise ({peer['by_type'].get('franchise',{}).get('n_branches','?')} สาขา) — ในเพดาน</div></div>
<div class="kpi"><div class="kpi-val red">{master_pct}%</div><div class="kpi-lbl">master ({peer['by_type'].get('master',{}).get('n_branches','?')} สาขา) — เกินเพดานทั้งกลุ่ม</div></div>
<div class="kpi"><div class="kpi-val red">3/3</div><div class="kpi-lbl">สาขาในรายงานเกินเพดาน {POLICY}%</div></div>
</div>
<strong style="font-size:13px">disc% รายสาขา master เทียบเพดาน {POLICY}%</strong>
<table style="margin-top:8px"><thead><tr><th>สาขา (master)</th><th class="num">disc%</th><th class="num">ส่วนลดรวม</th><th class="num">เกินเพดาน {POLICY}%</th><th class="center">สถานะ</th></tr></thead><tbody>""")
    for b in sorted(masters,key=lambda x:-x["pct"]):
        over=round(b["pct"]-POLICY,2)
        if b["branch_id"]==25: stat=badge("จับได้ (cash-pocketing)","#b91c1c")
        elif b["pct"]>POLICY: stat=badge("เกินเพดาน","#b45309")
        else: stat=badge("ในเพดาน","#15803d")
        nm_disp=b["name"]+(" ★" if b["branch_id"] in (24,26) else "")
        bg="background:#fdf2f2;" if b["pct"]>POLICY else ""
        add(f'<tr style="{bg}"><td><strong>{nm_disp}</strong></td><td class="num" style="font-weight:700">{b["pct"]}%</td><td class="num">{fmt(b["disc"])}</td><td class="num" style="color:{"#b91c1c" if over>0 else "#15803d"};font-weight:700">{"+"+str(over)+"%" if over>0 else str(over)+"%"}</td><td class="center">{stat}</td></tr>')
    add(f'</tbody></table>')
    add(f"""<div class="alert alert-red">
<strong>อ่านผล:</strong> เทียบ<u>เพดานนโยบาย {POLICY}%</u> — <strong>ทั้ง 3 สาขาเกินหมด</strong>: อุดร {udon['pct']}% (outlier + จับได้), สุรินทร์ {[b['pct'] for b in masters if b['branch_id']==24][0]}%, อุบลฯ {[b['pct'] for b in masters if b['branch_id']==26][0]}% (เกิน ฿{ud_exp:,.0f} — <u>ไม่ใช่ "ปกติ"</u>)
นอกจากปริมาณที่เกินเพดานแล้ว ยังมี<u>ลักษณะเชิงคุณภาพ</u>ที่ต้องสืบ (สุรินทร์ comp ไม่มีเอกสาร, การแชร์บัญชี, ปริมาณคูปองสิทธิ์อุบล) · ★ = สาขาในการสอบสวนนี้</div>
</div></div>""")

    # ── PAGE: Udon proven case + cash signature discriminator ─────────────────
    us=udon["cash_signature"]
    add(page_hdr("กรณีอ้างอิง: อุดรธานี (ทุจริตที่จับได้) + Signature เงินสด"))
    add(f"""<div class="sec-label">PROVEN CASE · CASH-POCKETING SIGNATURE</div>
<div class="sec-title" style="font-size:17px">อุดรธานี — modus operandi ที่ยืนยันแล้ว</div>
<div class="alert alert-red">
<strong>วิธีการที่จับได้ (confirmed):</strong> พนักงานกด <strong>ส่วนลดเด็ก</strong> ลงในบิล <u>หลังจากเช็คบิล/ปิดการขายเสร็จแล้ว</u>
แล้วนำ <strong>เงินสด</strong> ส่วนต่างเข้ากระเป๋า — ทำกับรายการที่ลูกค้า <strong>จ่ายเงินสด</strong> เป็นหลัก
(บิลจ่ายบัตร/โอน/QR ทำแบบนี้ไม่ได้ เพราะเงินเข้าบัญชีบริษัทตรง ไม่มีเงินสดให้หยิบ)</div>
<div class="kpi-grid">
<div class="kpi"><div class="kpi-val red">{udon['pct']}%</div><div class="kpi-lbl">disc% (สูงสุดในเครือ)</div></div>
<div class="kpi"><div class="kpi-val red">{fmt(udon['disc'])}</div><div class="kpi-lbl">ส่วนลดรวม 5 เดือน</div></div>
<div class="kpi"><div class="kpi-val red">{us['cash_share_disc']}%</div><div class="kpi-lbl">บิลส่วนลดที่เป็นเงินสด</div></div>
<div class="kpi"><div class="kpi-val red">{us['lift']}x</div><div class="kpi-lbl">cash-lift (เทียบบิลทั่วไป {us['cash_share_all']}%)</div></div>
</div>
<strong style="font-size:13px">Signature: จำนวนส่วนลดซ้ำของอุดร = % เงินสดสูง</strong>
<table style="margin-top:8px"><thead><tr><th class="num">฿ ส่วนลด</th><th class="num">ครั้ง</th><th class="num">% เงินสด</th><th>หมายเหตุ</th></tr></thead><tbody>""")
    for fx in us["fixed_amounts"]:
        cc="#dc2626" if fx["cash_pct"]>=40 else "#d97706"
        note="≈ ส่วนลดเด็ก/ครึ่งราคา" if fx["amt"] in (316,347,631) else ("คูณทวี" if fx["amt"]>1000 else "")
        add(f'<tr><td class="num" style="font-family:monospace;font-weight:700">฿{fx["amt"]:,.0f}</td><td class="num">{fx["n"]}</td><td class="num" style="color:{cc};font-weight:800">{fx["cash_pct"]}%</td><td style="font-size:11px;color:#64748b">{note}</td></tr>')
    add('</tbody></table>')
    # The discriminator: Surin/Ubon vs Udon
    add(f"""<div class="alert alert-blue">
<strong>นำ signature นี้ไปทดสอบ Surin/Ubon (cash-lift discriminator):</strong></div>
<table><thead><tr><th>สาขา</th><th class="num">% เงินสดของบิลส่วนลด</th><th class="num">% เงินสดบิลทั่วไป</th><th class="num">cash-lift</th><th class="center">match MO อุดร?</th></tr></thead><tbody>
<tr style="background:#fef2f2"><td><strong>อุดรธานี</strong> (จับได้แล้ว)</td><td class="num">{us['cash_share_disc']}%</td><td class="num">{us['cash_share_all']}%</td><td class="num" style="font-weight:800;color:#dc2626">{us['lift']}x</td><td class="center">{badge("✓ ใช่ (พิสูจน์แล้ว)","#dc2626")}</td></tr>
<tr><td><strong>สุรินทร์</strong></td><td class="num">{sb['cash_signature']['cash_share_disc']}%</td><td class="num">{sb['cash_signature']['cash_share_all']}%</td><td class="num" style="font-weight:800;color:#16a34a">{sb['cash_signature']['lift']}x</td><td class="center">{badge("✗ ไม่ match","#16a34a")}</td></tr>
<tr><td><strong>อุบลฯ</strong></td><td class="num">{ub['cash_signature']['cash_share_disc']}%</td><td class="num">{ub['cash_signature']['cash_share_all']}%</td><td class="num" style="font-weight:800;color:#16a34a">{ub['cash_signature']['lift']}x</td><td class="center">{badge("✗ ไม่ match","#16a34a")}</td></tr>
</tbody></table>
<div class="alert alert-am">
<strong>cash-signature ตัดออกได้แค่ MO เดียว — ไม่ใช่ใบรับรองความสะอาด:</strong> Surin/Ubon ไม่มี cash-concentration แบบอุดร (lift ~1.1x)
แปลว่า<u>ไม่ใช่</u> cash-pocketing แบบกดเงินสดหลังปิดบิลแบบอุดรเท่านั้น — <strong>แต่ละสาขามี MO ต่างกัน และ Surin/Ubon มีความผิดปกติเฉพาะตัวที่ร้ายแรงในแบบของมันเอง</strong> (ดูหน้า "3 สาขา 3 รูปแบบ")
<br>• <strong>อุบลฯ:</strong> Take Order (สิทธิ์ต่ำ) กดส่วนลดประเภทสิทธิ์สูง (ผู้บริหาร/ผู้ถือหุ้น/ผู้สูงอายุ 81+) = authorization inversion · 525 บิล ≥20% รวมครึ่งราคา ~50%
<br>• <strong>สุรินทร์:</strong> Manager+admin ให้ comp โดย {sd_neither_pct}% ของบิลไม่ผูกคูปอง/เหตุผล = ผิดปกติที่สุด (สิทธิ์สูง + ไม่มีเอกสาร + สลับ account)</div>
</div></div>""")

    # ── PAGE: THREE DISTINCT FRAUD PATTERNS (owner's first-hand observations) ──
    sb=robust["branches"]["surin"]; ub=robust["branches"]["ubon"]; udon=robust["udon"]
    add(page_hdr("3 สาขา 3 รูปแบบทุจริต — ไม่เหมือนกันเลย"))
    add(f"""<div class="sec-label">THREE DISTINCT MODI OPERANDI</div>
<div class="sec-title" style="font-size:18px">แต่ละสาขามีลักษณะการทุจริต/ช่องโหว่ต่างกัน</div>
<div class="alert alert-red"><strong>หัวใจของรายงาน:</strong> อย่าเหมารวมหรือใช้ test เดียวตัดสินทุกสาขา —
cash-signature จับ MO อุดรได้ แต่ <u>ไม่ครอบคลุม</u> MO ของสุรินทร์/อุบลฯ ซึ่งเป็นคนละแบบและร้ายแรงในตัวเอง</div>
<table><thead><tr><th style="width:14%">สาขา</th><th style="width:29%">รูปแบบ (MO)</th><th style="width:28%">หลักฐานจากข้อมูล</th><th style="width:29%">test ที่ใช้จับ / ต้องใช้</th></tr></thead><tbody>
<tr style="background:#fef2f2">
<td><strong>อุดรธานี</strong><br><span style="font-size:10px;color:#94a3b8">master · พิสูจน์แล้ว</span></td>
<td><strong>ยักเงินสดผ่านคูปองเด็ก:</strong> กดคูปอง<u>เด็ก (#33)</u>หลังปิดบิล เก็บ<u>เงินสด</u>ส่วนต่าง บิลเงินสด</td>
<td>คูปอง #33 = {next((c['bills'] for c in robust['udon']['coupons'] if c['sid']==33),0):,} บิล ฿{next((c['amt'] for c in robust['udon']['coupons'] if c['sid']==33),0)/1e6:.1f}M ผ่าน admin · cash-lift <strong>1.87x</strong> · disc% 7.32% สูงสุดเครือ</td>
<td>✅ cash-signature + เจ้าของยืนยัน (สอบทานตรง) → ขยายผล/ดำเนินการ</td></tr>
<tr style="background:#fff7ed">
<td><strong>อุบลราชธานี</strong><br><span style="font-size:10px;color:#94a3b8">master · ต้องสืบ</span></td>
<td><strong>คูปองสิทธิ์ปริมาณสูง ผ่าน Take Order:</strong> ผู้สูงอายุ81+/เด็ก/ผู้ถือหุ้น/ผู้บริหาร — ทุกบิลผูกคูปอง (มี trail) แต่ปริมาณ/ที่นั่งผิดสัดส่วน</td>
<td>ผู้สูงอายุ81+ (#35) = {ub_eld['bills']} บิล แต่ {ub_eld['covers']:,} ที่นั่ง ({round(ub_eld['covers']/max(ub_eld['bills'],1),1)}/บิล) · Take Order กดเป็นหลัก · co-occur ที่ master อื่นด้วย</td>
<td>⚠ vouch หลักฐานคุณสมบัติ (บัตร ปชช./อายุ) + จำกัดสิทธิ์ Take Order</td></tr>
<tr style="background:#fef2f2">
<td><strong>สุรินทร์</strong><br><span style="font-size:10px;color:#94a3b8">master · ผิดปกติสุด</span></td>
<td><strong>comp ไม่มีเอกสาร ผ่านบัญชีสิทธิ์สูง:</strong> ส่วนลดไม่ผูกทั้งคูปองและเหตุผล ผ่าน <code>admin</code>+<code>Manager</code> สลับวัน</td>
<td><strong>{sd_neither_pct}%</strong> ({sd_att.get('neither_n',0):,}/{sd_att.get('disc_bills',0):,} บิล) ไม่มี attribution ฿{sd_att.get('neither_amt',0)/1e3:.0f}K · co-occur เพียง <strong>{sb['alternation']['cooccurrence_rate']}%</strong></td>
<td>⚠ vouch เอกสารอนุมัติ comp + POS login log + CCTV</td></tr>
</tbody></table>
<div class="three-col" style="margin-top:6px">
<div class="alert alert-red" style="margin:0"><strong>อุดร = ยักเงินสด</strong><br><span style="font-size:11px">เงินออกจากลิ้นชักจริง · มี victim ชัด (บริษัท) · พิสูจน์ทางการเงินได้</span></div>
<div class="alert alert-am" style="margin:0"><strong>อุบลฯ = ปริมาณคูปองสิทธิ์</strong><br><span style="font-size:11px">มี trail แต่ผู้สูงอายุ81+/เด็ก ปริมาณ/ที่นั่งสูงผิดสัดส่วน — เสี่ยง comp พวกพ้อง/ปลอมสิทธิ์ ต้อง vouch</span></div>
<div class="alert alert-red" style="margin:0"><strong>สุรินทร์ = ไม่มีเอกสาร</strong><br><span style="font-size:11px">comp ผ่านบัญชีสิทธิ์สูงโดยไม่ผูกคูปอง/เหตุผล — ต่างจาก 2 สาขาที่มี trail = anomaly เอกสารแท้จริง</span></div>
</div>
<div class="alert alert-blue"><strong>ข้อสังเกตสำคัญ (แก้ความเข้าใจเดิม):</strong> ระบบ <u>มี</u> การบันทึกประเภทส่วนลด 2 ช่อง — <code>discount_reason_id</code> (%ทั่วไป) และ <code>coupon_series_id</code> (คูปองตั้งชื่อ เช่น ผู้สูงอายุ81+/เด็ก/ผู้ถือหุ้น)
อุบลฯ/อุดร ผูกคูปองครบทุกบิล · มีเพียง<u>สุรินทร์</u>ที่ {sd_neither_pct}% ของบิลไม่ผูกอะไรเลย = ช่องโหว่เอกสารเฉพาะสุรินทร์</div>
</div></div>""")


def _add_perperson_overview(add, robust):
    """Appendix intro: raw-vs-adjusted per-person ranking table (the correction)."""
    rb_branch=robust["branches"]; bonf=robust["bonferroni"]
    add(page_hdr("ภาคผนวก ก — ภาพรวมรายบุคคล (raw vs ปรับแก้)"))
    add(f"""<div class="sec-label">Appendix A · per-person corrected</div>
<div class="sec-title" style="font-size:16px">การเข้างานรายบุคคล vs ส่วนลด — ก่อนและหลังควบคุมตัวแปรกวน</div>
<div class="alert alert-blue">
<strong>สำคัญ:</strong> ส่วนลดรายวัน<u>สัมพันธ์อย่างแรงกับยอดขาย</u> (Pearson r = {rb_branch.get('surin',{}).get('revenue_confound',{}).get('r','?')} สุรินทร์, {rb_branch.get('ubon',{}).get('revenue_confound',{}).get('r','?')} อุบลฯ)
และเสาร์-อาทิตย์ disc% สูงกว่าวันธรรมดา ~{rb_branch.get('surin',{}).get('dow_premium',{}).get('premium','?')}x
การเทียบ "ยอดส่วนลดดิบ" จึงให้ภาพลวง — เมื่อปรับเป็น disc% + permutation + Bonferroni ({bonf['n_tests']} คน, p&lt;{bonf['threshold']:.4f}) สัญญาณเกือบทั้งหมดหายไป</div>
<table>
<thead><tr><th>ชื่อ</th><th>ตำแหน่ง</th><th>สาขา</th>
<th class="num">Lift ดิบ</th><th class="num">Lift ปรับ</th>
<th class="center">p (ปรับ)</th><th class="center">Bonferroni</th><th>ข้อสรุป</th></tr></thead><tbody>""")
    for p in sorted(robust["people"],key=lambda x:-(x["pct_lift"] or 0)):
        bname="สุรินทร์" if p["branch"]=="surin" else "อุบลฯ"
        pc=lift_color(p["pct_lift"] or 1)
        bonf_mark=badge("ผ่าน","#b91c1c") if p["bonferroni_pass"] else badge("ตก","#15803d")
        vcol="#b45309" if "confounded" in p["verdict"] else ("#b91c1c" if "ควรสืบเพิ่ม" in p["verdict"] else "#15803d")
        add(f'<tr><td><strong>{p["nick"]}</strong></td><td style="font-size:11px">{p["role"]}</td><td style="font-size:11px">{bname}</td><td class="num" style="color:#94a3b8;text-decoration:line-through">{p["raw_lift"]}x</td><td class="num" style="color:{pc};font-weight:800">{p["pct_lift"]}x</td><td class="center">{p_badge(p["p_pct"])}</td><td class="center">{bonf_mark}</td><td style="font-size:11px;color:{vcol};font-weight:600">{p["verdict"]}</td></tr>')
    add("""</tbody></table>
<div class="alert alert-red">
<strong>ข้อสรุปที่ป้องกันได้:</strong> หลังควบคุมยอดขาย + วันในสัปดาห์ + แก้ multiple-comparison
<u>ไม่มีพนักงานคนใดที่การเข้างานสัมพันธ์กับส่วนลดอย่างมีนัยสำคัญแบบที่อธิบายด้วยตารางกะไม่ได้</u> —
รายเดียวที่ผ่าน Bonferroni (ส้ม) อธิบายได้ด้วยการหยุดวันพุธ (disc% ต่ำสุด) + ทำงานเสาร์-อาทิตย์ครบ (disc% สูงสุด)
สัญญาณ "ผู้ต้องสงสัย" จากการวิเคราะห์ดิบจึงเป็น artifact ไม่ใช่หลักฐานชี้ตัว</div>""")
    add('</div></div>')


def _add_methodology_pages(add, robust, chain):
    """Methodology, confounding diagnosis, account-alternation, fixed-amount xtab, limitations."""
    sb=robust["branches"]["surin"]; ub=robust["branches"]["ubon"]
    bonf=robust["bonferroni"]
    sd_neither_pct=round(100*sb["attribution"]["neither_n"]/max(sb["attribution"]["disc_bills"],1))

    # ── PAGE: Methodology & why raw analysis misleads ────────────────────────
    add(page_hdr("ระเบียบวิธีและตัวแปรกวน (Methodology & Confounding)"))
    add(f"""<div class="sec-label">METHODOLOGY · WHY THE RAW ANALYSIS MISLEADS</div>
<div class="sec-title" style="font-size:18px">ทำไม "Lift ดิบ" จึงให้ภาพลวง และเราแก้อย่างไร</div>
<div class="alert alert-blue">รายงานนี้แยกชัดเจนระหว่างสิ่งที่ <strong>พิสูจน์ได้</strong> (ระดับสาขา) กับสิ่งที่ <strong>พิสูจน์ไม่ได้จากข้อมูลนี้</strong> (การชี้ตัวบุคคล)
เพื่อให้ใช้เป็นหลักฐานได้จริงโดยไม่กล่าวหาผิดคน</div>
<table><thead><tr><th>ปัญหาของวิธีดิบ</th><th>ผลกระทบ</th><th>วิธีแก้ในรายงานนี้</th></tr></thead><tbody>
<tr><td><strong>1. Revenue confound</strong><br>ใช้ยอดส่วนลด<u>ดิบ</u></td>
<td>ส่วนลดสัมพันธ์กับยอดขายแรงมาก (r={sb['revenue_confound']['r']} สุรินทร์, r={ub['revenue_confound']['r']} อุบลฯ) — วันขายดี=ส่วนลดเยอะอัตโนมัติ</td>
<td>ใช้ <strong>disc% = ส่วนลด ÷ ยอดขาย</strong> แทนยอดดิบ</td></tr>
<tr><td><strong>2. Day-of-week confound</strong></td>
<td>เสาร์-อาทิตย์ disc% สูงกว่าวันธรรมดา {sb['dow_premium']['premium']}x (สุรินทร์) — คนที่หยุดวันธรรมดาจะดู "ส่วนลดสูงตอนทำงาน" เทียม</td>
<td>วินิจฉัย off-day DOW skew ของแต่ละคน + ระบุ artifact</td></tr>
<tr><td><strong>3. Wrong test / no correction</strong></td>
<td>t-test สมมุติ normal; ทดสอบ 9 คนพร้อมกัน → false positive พุ่ง</td>
<td><strong>Permutation test</strong> (20,000 รอบ, distribution-free) + <strong>Bonferroni</strong> (เกณฑ์ p&lt;{bonf['threshold']:.4f})</td></tr>
<tr><td><strong>4. Unit-of-analysis ผิด</strong></td>
<td>ยอดส่วนลด "ทั้งสาขา" ในวันที่คนทำงาน ≠ ส่วนลดที่ "คนนั้น" กด (มีเพื่อนร่วมงานอีกหลายคนวันเดียวกัน)</td>
<td>Exclusive-day analysis + ระบุว่า POS account ≠ ตัวบุคคล</td></tr>
<tr><td><strong>5. ไม่มี POS→พนักงาน mapping</strong></td>
<td>field <code>cashier</code> = ชื่อ account (Manager/admin/Take Order) ไม่ใช่รหัสพนักงาน; ตาราง pos_staff_month มี staff_name = ว่าง</td>
<td>ยอมรับข้อจำกัด — แนะนำดึง POS login log จาก Easy</td></tr>
</tbody></table>
<div class="two-col"><div>
<strong>disc% ตามวันในสัปดาห์ — สุรินทร์</strong><div style="height:8px"></div>""")
    mx=max(list(sb['dow_premium']['by_dow'].values())+[1])
    for k in range(7):
        v=sb['dow_premium']['by_dow'].get(k,0)
        c="#dc2626" if k>=5 else "#1e3a5f"
        add(bar_row(DOW_TH_FULL[k][:2]+".",v,mx,c,val_fmt=f"{v}%"))
    add('</div><div><strong>disc% ตามวันในสัปดาห์ — อุบลฯ</strong><div style="height:8px"></div>')
    mx2=max(list(ub['dow_premium']['by_dow'].values())+[1])
    for k in range(7):
        v=ub['dow_premium']['by_dow'].get(k,0)
        c="#dc2626" if k>=5 else "#ea580c"
        add(bar_row(DOW_TH_FULL[k][:2]+".",v,mx2,c,val_fmt=f"{v}%"))
    add('</div></div>')
    add('</div></div>')

    # ── PAGE: Account alternation (the SOLID structural finding) ──────────────
    add(page_hdr("การสลับ POS Account — หลักฐานเชิงโครงสร้าง"))
    add(f"""<div class="sec-label">ACCOUNT ALTERNATION · STRUCTURAL EVIDENCE</div>
<div class="sec-title" style="font-size:18px">account ที่ให้ส่วนลด "ทำงานพร้อมกัน" หรือ "สลับวัน"?</div>
<div class="sec-desc">นี่คือสิ่งที่ <strong>ข้อมูลพิสูจน์ได้</strong> โดยไม่ต้องอาศัยตารางเข้างาน — ดูว่าแต่ละวันมี account ใดให้ส่วนลดบ้าง</div>""")
    for br,b,c1,c2 in [("สุรินทร์",sb,"#1e3a5f","#dc2626"),("อุบลฯ",ub,"#ea580c","#1e3a5f")]:
        a=b["alternation"]
        add(f"""<div style="margin-bottom:14px"><strong style="font-size:14px">{br}: <code>{a['accounts'][0]}</code> vs <code>{a['accounts'][1]}</code></strong>
<div class="kpi-grid" style="margin-top:8px">
<div class="kpi"><div class="kpi-val">{a['total_disc_days']}</div><div class="kpi-lbl">วันที่มีส่วนลดทั้งหมด</div></div>
<div class="kpi"><div class="kpi-val em">{a['both']}</div><div class="kpi-lbl">วันที่ทั้งคู่ให้ส่วนลด (co-occur {a['cooccurrence_rate']}%)</div></div>
<div class="kpi"><div class="kpi-val red">{a['only_a']}</div><div class="kpi-lbl">เฉพาะ {a['accounts'][0]}</div></div>
<div class="kpi"><div class="kpi-val red">{a['only_b']}</div><div class="kpi-lbl">เฉพาะ {a['accounts'][1]}</div></div>
</div></div>""")
    add(f"""<div class="alert alert-red">
<strong>สุรินทร์ — สัญญาณแข็ง:</strong> co-occurrence เพียง {sb['alternation']['cooccurrence_rate']}% (solo {sb['alternation']['solo_rate']}%)
สอง account ระดับสูง (Manager/admin) แทบไม่เคยให้ส่วนลดในวันเดียวกัน — <u>สลับวันชัดเจน</u>
สอดคล้องกับสมมุติฐาน "คนคนเดียว/กลุ่มเดียวที่ถือรหัสทั้งสอง account" หรือ "การแชร์ credential"</div>
<div class="alert alert-am">
<strong>อุบลฯ — ต่างออกไป:</strong> co-occurrence {ub['alternation']['cooccurrence_rate']}% — Take Order และ Cashier มักให้ส่วนลด<u>ในวันเดียวกัน</u>
จึงไม่ใช่รูปแบบสลับ แต่เป็นการที่ <strong>หลาย account ใช้สิทธิ์ส่วนลดขนานกัน</strong> (ปัญหาหลัก = Take Order ไม่ควรมีสิทธิ์เลย)</div>
</div></div>""")

    # ── PAGE: Fixed-amount cross-tab + Limitations ───────────────────────────
    add(page_hdr("จำนวนส่วนลดซ้ำ × Account + ข้อจำกัด"))
    add(f"""<div class="sec-label">FIXED-AMOUNT CROSS-TAB</div>
<div class="sec-title" style="font-size:16px">จำนวนเงินส่วนลดที่ซ้ำบ่อย ผูกกับ account เดียวหรือกระจาย?</div>
<div class="sec-desc">ถ้าจำนวนเดิมกระจายหลาย account = systematic (สอดคล้องกับคูปองราคาบุฟเฟ่ต์/ครึ่งราคาที่กำหนดไว้) มากกว่าการปั้นตัวเลขของคนเดียว</div>""")
    for br,b in [("สุรินทร์",sb),("อุบลฯ",ub)]:
        add(f'<strong style="font-size:13px">{br}</strong><table style="margin-top:6px"><thead><tr><th class="num">฿ ส่วนลด</th><th class="num">ครั้งรวม</th><th>การกระจายตาม account</th><th class="num">account สูงสุด</th></tr></thead><tbody>')
        for row in b["fixed_xtab"][:6]:
            accs=", ".join(f'{u}={n}' for u,n in row["accounts"][:4])
            spread="กระจาย" if row["top_share"]<70 else "กระจุก"
            sc="#16a34a" if row["top_share"]<70 else "#dc2626"
            add(f'<tr><td class="num" style="font-family:monospace;font-weight:700">฿{row["amt"]:,.0f}</td><td class="num">{row["total"]}</td><td style="font-size:11px">{accs}</td><td class="num" style="color:{sc};font-weight:700">{row["top_share"]:.0f}% ({spread})</td></tr>')
        add('</tbody></table>')
    add(f"""<div class="alert alert-am">
<strong>ตีความ:</strong> จำนวนซ้ำหลักของทั้งสองสาขา (เช่น ฿631, ฿347, ฿316, ฿694) <u>กระจายข้ามหลาย account</u> ไม่ผูกกับคนเดียว
และเป็นชุดตัวเลขที่ตรงกับ<strong>คูปองที่กำหนดราคาไว้</strong> (เด็ก/ผู้สูงอายุ/ครึ่งราคาบุฟเฟ่ต์) — สอดคล้องกับการใช้คูปองจริง (มี trail)
มากกว่าการกุตัวเลขลอย ๆ — ซึ่งเปลี่ยนทิศการสืบจาก "ปั้นบิลปลอม" เป็น "ให้ส่วนลดจริงโดยไม่มีสิทธิ์/ไม่มีเอกสาร"</div>
<div class="sec-label" style="margin-top:14px">LIMITATIONS</div>
<div class="sec-title" style="font-size:15px">ข้อจำกัดของการวิเคราะห์ (ต้องระบุในรายงานสืบสวน)</div>
<ul style="padding-left:18px;line-height:2;font-size:12px">
<li><strong>ไม่มีการ map POS account → พนักงานรายคน</strong> ใน DB — ชี้ตัวจากข้อมูลนี้ไม่ได้</li>
<li><strong>Exclusive-day = 0 ทุกคน</strong> — พนักงานที่ติดตามทำงานวันซ้อนกันเสมอ จึงแยกไม่ได้ว่าใครกดส่วนลดวันใด</li>
<li><strong>ตารางเข้างานเป็นกะที่วางแผนล่วงหน้า</strong> ไม่ใช่เวลา login จริง — คนอาจมา/ไม่มาต่างจากตาราง</li>
<li><strong>ประเภทส่วนลดมี 2 ช่อง</strong> (discount_reason_id + coupon_series_id) — อุบล/อุดร ผูกคูปองครบ, สุรินทร์ {sd_neither_pct}% ไม่ผูก; การ map คูปอง→ผู้ใช้สิทธิ์จริงต้องใช้เอกสารนอกระบบ</li>
<li><strong>ข้อมูลเริ่ม ม.ค. 2026</strong> + ไฟล์กะผู้จัดการสุรินทร์เริ่ม ก.พ. — เดือนแรกข้อมูลบางส่วนขาด</li>
</ul>
<div class="alert alert-red"><strong>เส้นทางหาหลักฐานชี้ตัวที่แท้จริง:</strong> (1) ดึง POS login/audit log จาก Easy ว่าใคร login เป็น Manager/admin/Take Order แต่ละวัน-เวลา ·
(2) เทียบเวลา timestamp บิลส่วนลดกับ CCTV · (3) สัมภาษณ์ + ขอเอกสารประกอบส่วนลดพิเศษย้อนหลัง</div>
</div></div>""")


def page_hdr(title):
    return (f'<div class="page"><div class="sec-body">'
            f'<div class="sec-hdr"><span class="sec-hdr-title">{title}</span>'
            f'<span class="sec-hdr-brand">OSHINEI · INTERNAL AUDIT · CONFIDENTIAL</span></div>')


# ── Main ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.path.join(os.path.dirname(__file__), "oshinei.db"))
    ap.add_argument("--out", default=os.path.join(os.path.dirname(__file__), "..", "docs", "SURIN_UBON_FRAUD.html"))
    args = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"): sys.stdout.reconfigure(encoding="utf-8")
    print("📥 อ่านตารางเข้างาน...")
    att = load_attendance()
    for eid,_,_,_,nick,_ in EMPLOYEES:
        w=sum(1 for v in att.get(eid,{}).values() if v)
        print(f"  {nick}: {w}/{len(att.get(eid,{}))} วัน")
    print("🗄 ดึงข้อมูล DB...")
    db_data = load_db(args.db)
    print(f"  สุรินทร์: {db_data['surin']['disc_bills']:,} บิล / {db_data['ubon']['disc_bills']:,} อุบลฯ")
    print("🧪 วิเคราะห์ robust (revenue-adjusted + permutation + Bonferroni)...")
    import fraud_analysis
    robust = fraud_analysis.analyze(args.db, EMPLOYEES, BRANCH_IDS, DATE_FROM, DATE_TO, load_attendance)
    n_pass = sum(1 for p in robust["people"] if p["bonferroni_pass"])
    print(f"  Bonferroni: {robust['bonferroni']['n_tests']} คน, ผ่าน {n_pass} คน")
    print("📊 สร้างรายงาน HTML...")
    html = generate(att, db_data, robust)
    out = os.path.abspath(args.out)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f: f.write(html)
    print(f"✅ สร้างสำเร็จ: {out} ({os.path.getsize(out):,} bytes)")
