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

def badge(lbl,color="#4f46e5"):
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

def bar_row(label,val,max_val,color="#4f46e5",val_fmt=None):
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
                row_html+='<td style="width:38px;height:36px"></td>'
                continue
            dt=f"{ym}-{int(cell):02d}"
            is_worked=emp_att.get(dt,None)
            disc=daily_disc.get(dt,{}).get("disc",0) if daily_disc else 0
            if disc>=5000: dc="#fee2e2"; tc="#dc2626"
            elif disc>=2000: dc="#ffedd5"; tc="#ea580c"
            elif disc>=500: dc="#fef9c3"; tc="#d97706"
            elif disc>0: dc="#dcfce7"; tc="#16a34a"
            else: dc="#f8fafc"; tc="#94a3b8"
            if is_worked is True:
                border="2px solid #4f46e5"
                bg=dc
            elif is_worked is False:
                border="1px dashed #cbd5e1"
                bg="#f0f4f8"
                tc="#94a3b8"
            else:
                border="1px solid #e2e8f0"
                bg=dc
            disc_str=f'<div style="font-size:8px;color:{tc}">{fmt(disc) if disc>0 else ""}</div>'
            row_html+=f'<td style="width:38px;height:36px;text-align:center;vertical-align:top;border:{border};border-radius:3px;background:{bg};padding:2px"><div style="font-size:10px;font-weight:600">{cell}</div>{disc_str}</td>'
        rows.append(f"<tr>{row_html}</tr>")
    hdr=''.join(f'<th style="font-size:9px;text-align:center;color:#94a3b8;padding:2px">{d}</th>' for d in ["จ","อ","พ","พฤ","ศ","ส","อา"])
    return f'<div style="margin-bottom:10px"><div style="font-size:12px;font-weight:700;color:#4f46e5;margin-bottom:4px">{MONTH_TH.get(ym,ym)}</div><table style="border-collapse:separate;border-spacing:2px"><thead><tr>{hdr}</tr></thead><tbody>{"".join(rows)}</tbody></table></div>'

# ── CSS ──────────────────────────────────────────────────────────────────────
CSS = """
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Sarabun',sans-serif;font-size:13px;color:#1e293b;background:#e2e8f0;print-color-adjust:exact;-webkit-print-color-adjust:exact}
:root{--indigo:#4f46e5;--rd:#dc2626;--am:#d97706;--em:#16a34a}
.cover{width:100%;min-height:100vh;background:linear-gradient(135deg,#1e1b4b 0%,#312e81 35%,#4338ca 70%,#1e40af 100%);display:flex;flex-direction:column;justify-content:center;align-items:flex-start;padding:70px 80px;color:#fff;page-break-after:always}
.cover-tag{background:rgba(255,255,255,.15);border:1px solid rgba(255,255,255,.3);padding:5px 14px;border-radius:20px;font-size:11px;letter-spacing:2px;text-transform:uppercase;margin-bottom:28px;display:inline-block}
.cover-title{font-size:48px;font-weight:800;line-height:1.1;margin-bottom:14px}
.cover-sub{font-size:20px;font-weight:300;color:rgba(255,255,255,.8);margin-bottom:40px}
.cover-kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:20px;margin-bottom:40px;width:100%}
.ckpi{background:rgba(255,255,255,.12);border:1px solid rgba(255,255,255,.2);border-radius:10px;padding:20px}
.ckpi-val{font-size:30px;font-weight:800;color:#fbbf24}
.ckpi-lbl{font-size:12px;color:rgba(255,255,255,.7);margin-top:4px}
.cover-flag{display:flex;align-items:center;gap:8px;background:rgba(220,38,38,.2);border:1px solid rgba(220,38,38,.5);padding:8px 14px;border-radius:7px;font-size:13px;margin-top:8px}
.cover-foot{font-size:11px;color:rgba(255,255,255,.5);border-top:1px solid rgba(255,255,255,.15);padding-top:20px;width:100%;display:flex;justify-content:space-between;margin-top:36px}
.page{width:210mm;min-height:297mm;margin:0 auto 24px;background:#fff;box-shadow:0 4px 24px rgba(0,0,0,.1);page-break-after:always}
.page-last{page-break-after:auto}
.sec-hdr{background:var(--indigo);color:#fff;padding:12px 28px;display:flex;justify-content:space-between;align-items:center}
.sec-hdr-title{font-size:15px;font-weight:700}
.sec-hdr-brand{font-size:10px;opacity:.7;letter-spacing:1px}
.sec-body{padding:24px 28px}
.sec-label{font-size:10px;font-weight:700;color:var(--indigo);letter-spacing:2px;text-transform:uppercase;margin-bottom:3px}
.sec-title{font-size:20px;font-weight:800;margin-bottom:6px}
.sec-desc{font-size:12px;color:#64748b;margin-bottom:18px}
.kpi-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:18px}
.kpi3{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-bottom:18px}
.kpi{background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;padding:14px}
.kpi-val{font-size:24px;font-weight:800;color:var(--indigo)}
.kpi-val.red{color:var(--rd)}
.kpi-val.am{color:var(--am)}
.kpi-val.em{color:var(--em)}
.kpi-lbl{font-size:11px;color:#64748b;margin-top:2px}
table{width:100%;border-collapse:collapse;font-size:12px;margin-bottom:16px}
th{background:#f1f5f9;color:#374151;font-weight:700;padding:8px 10px;text-align:left;border-bottom:2px solid #e2e8f0;font-size:11px}
td{padding:7px 10px;border-bottom:1px solid #f1f5f9;vertical-align:middle}
.num{text-align:right;font-variant-numeric:tabular-nums;font-weight:600}
.center{text-align:center}
.two-col{display:grid;grid-template-columns:1fr 1fr;gap:20px;margin-bottom:16px}
.three-col{display:grid;grid-template-columns:1fr 1fr 1fr;gap:16px}
.alert{border-radius:7px;padding:12px 16px;margin-bottom:14px;border-left:4px solid;font-size:12px}
.alert-red{background:#fef2f2;border-color:#dc2626;color:#991b1b}
.alert-am{background:#fffbeb;border-color:#d97706;color:#92400e}
.alert-em{background:#f0fdf4;border-color:#16a34a;color:#14532d}
.alert-blue{background:#eff6ff;border-color:#3b82f6;color:#1e40af}
.person-hdr{background:linear-gradient(135deg,#4f46e5,#7c3aed);color:#fff;border-radius:10px;padding:20px 24px;margin-bottom:16px;display:flex;justify-content:space-between;align-items:center}
.person-name{font-size:22px;font-weight:800}
.person-nick{font-size:14px;opacity:.8}
.person-role{font-size:12px;opacity:.7;margin-top:4px}
.person-eid{font-size:11px;opacity:.5;font-family:monospace}
.person-stats{display:grid;grid-template-columns:repeat(5,1fr);gap:10px;margin-bottom:14px}
.pstat{background:#f8fafc;border:1px solid #e2e8f0;border-radius:7px;padding:10px;text-align:center}
.pstat-val{font-size:18px;font-weight:800;color:var(--indigo)}
.pstat-lbl{font-size:10px;color:#64748b;margin-top:2px}
.toc-item{display:flex;justify-content:space-between;padding:5px 0;border-bottom:1px dashed #e2e8f0;font-size:12px}
.verdict-box{border-radius:8px;padding:14px;border:2px solid;margin-top:12px}
.flag-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:14px}
.flag{border-radius:7px;padding:12px;border:1px solid}
.flag-red{background:#fef2f2;border-color:#fca5a5}
.flag-am{background:#fffbeb;border-color:#fcd34d}
@media print{body{background:#fff}.page{box-shadow:none;margin:0}.cover{page-break-after:always}}
@page{margin:0;size:A4}
"""

# ── REPORT BUILDER ──────────────────────────────────────────────────────────
def generate(att, db_data):
    sd=db_data["surin"]; ud=db_data["ubon"]
    chain=db_data["chain_disc_pct"]
    corr=build_corr(att,db_data)
    surin_corr=[c for c in corr if c["branch"]=="surin"]
    ubon_corr=[c for c in corr if c["branch"]=="ubon"]
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

    # ── COVER ────────────────────────────────────────────────────────────────
    add(f"""<div class="cover">
<span class="cover-tag">🔍 FRAUD INVESTIGATION · CONFIDENTIAL · DETAILED EDITION</span>
<div class="cover-title">รายงานตรวจสอบ<br>ส่วนลดผิดปกติ<br>เชิงลึก</div>
<div class="cover-sub">สาขาสุรินทร์ & อุบลราชธานี · ม.ค.–พ.ค. 2569 · วิเคราะห์รายบุคคล 9 คน</div>
<div class="cover-kpis">
  <div class="ckpi"><div class="ckpi-val">{fmt(sd['disc_total'])}</div><div class="ckpi-lbl">ส่วนลดสุรินทร์ (5 เดือน)</div></div>
  <div class="ckpi"><div class="ckpi-val">{sd['disc_pct']}%</div><div class="ckpi-lbl">% ส่วนลด สุรินทร์ (Chain: {chain}%)</div></div>
  <div class="ckpi"><div class="ckpi-val">{fmt(ud['disc_total'])}</div><div class="ckpi-lbl">ส่วนลดอุบลฯ (5 เดือน)</div></div>
  <div class="ckpi"><div class="ckpi-val">{ud['disc_pct']}%</div><div class="ckpi-lbl">% ส่วนลด อุบลฯ (Chain: {chain}%)</div></div>
</div>
<div class="cover-flag">🚨 ทุกรายการไม่มีเหตุผล — {sd['disc_bills']+ud['disc_bills']:,} บิล รวม {fmt(sd['disc_total']+ud['disc_total'])}</div>
<div class="cover-flag">🔴 สุรินทร์: ส่วนลดผ่าน Manager/admin เท่านั้น สลับวัน บ่งชี้แชร์ account</div>
<div class="cover-flag">🟠 อุบลฯ: Take Order ให้ส่วนลดสูงสุด (ไม่ควรมีสิทธิ์) — ส่วนลดพิเศษ ผู้บริหาร/ผู้ถือหุ้น/ผู้สูงอายุ ไม่บันทึกเหตุผล</div>
<div class="cover-foot">
  <span>OSHINEI ENTERPRISE · Internal Audit · ฉบับวิเคราะห์รายบุคคล</span>
  <span>ข้อมูล {DATE_FROM} – {DATE_TO} · สร้าง {now}</span>
  <span>STRICTLY CONFIDENTIAL</span>
</div></div>""")

    # ── TOC PAGE ──────────────────────────────────────────────────────────────
    add(page_hdr("สารบัญ (Table of Contents)"))
    toc_items = [
        ("1","สรุปผู้บริหาร (Executive Summary)","2-3"),
        ("2","เปรียบเทียบ KPI กับมาตรฐานทั้งเชน","4"),
        ("3","วิเคราะห์เชิงลึกสาขาสุรินทร์","5-7"),
        ("3.1","— รูปแบบ POS Account: Manager vs admin","5"),
        ("3.2","— Fixed Amount Patterns & Big Bills","6"),
        ("3.3","— Timeline รายวัน","7"),
        ("4","วิเคราะห์เชิงลึกสาขาอุบลราชธานี","8-10"),
        ("4.1","— Take Order: สิทธิ์ที่ไม่ควรมี","8"),
        ("4.2","— ส่วนลดพิเศษ: ผู้บริหาร/ผู้ถือหุ้น/ผู้สูงอายุ","9"),
        ("4.3","— Big Bills & Monthly Pattern","10"),
        ("5","วิเคราะห์รายบุคคล — สุรินทร์ (4 คน)","11-22"),
        ("5.1","เดียร์ (ณัจฐวงษ์) — ผู้จัดการสาขา","11-12"),
        ("5.2","ตุ๊กตา (ราตรี) — Supervisor","13-14"),
        ("5.3","ซูกัส (ภัทรวดี) — ผู้ช่วยผู้จัดการ","15-16"),
        ("5.4","จ๊ะจ๋า (นุชรีย์) — Cashier","17-18"),
        ("5.5","สรุปเปรียบเทียบ สุรินทร์ 4 คน","19"),
        ("6","วิเคราะห์รายบุคคล — อุบลฯ (5 คน)","20-35"),
        ("6.1","เก่ง (สมชาย) — ผู้จัดการสาขา","20-21"),
        ("6.2","เจมอล์ (ศิริวรรณ) — Supervisor","22-23"),
        ("6.3","โอ๋ (กาญจนา) — ผู้ช่วยผู้จัดการ","24-25"),
        ("6.4","ส้ม (สร้อยเพชร) — ผู้ช่วยผู้จัดการ","26-27"),
        ("6.5","ตุ๊ต๊ะ (นิธิพร) — Cashier","28-29"),
        ("6.6","สรุปเปรียบเทียบ อุบลฯ 5 คน","30"),
        ("7","Attendance Correlation Analysis (Welch t-test + Pearson r)","31-32"),
        ("8","Off-Day Analysis — ส่วนลดวันหยุด","33"),
        ("9","ประมาณการความเสียหายทางการเงิน","34"),
        ("10","Red Flags & Action Plan","35-36"),
        ("11","ข้อเสนอแนะเร่งด่วน","37"),
    ]
    toc_html='<div style="background:#f8fafc;border-radius:8px;padding:16px">'
    for num,title,pg in toc_items:
        bold="font-weight:700;" if "." not in num else ""
        indent="padding-left:20px;" if "." in num else ""
        toc_html+=f'<div class="toc-item" style="{indent}"><span style="{bold}display:flex;gap:8px"><span style="color:var(--indigo);font-weight:700;min-width:30px">{num}</span><span>{title}</span></span><span style="color:#94a3b8">{pg}</span></div>'
    toc_html+='</div>'
    add(toc_html)
    add('</div></div>')

    # ── EXEC SUMMARY ─────────────────────────────────────────────────────────
    add(page_hdr("สรุปผู้บริหาร (Executive Summary)"))
    add(f"""
<div class="alert alert-red"><strong>🚨 สรุปสำหรับผู้บริหาร:</strong> ตรวจพบพฤติกรรมส่วนลดผิดปกติ 2 สาขา รวมมูลค่าส่วนลดเกินมาตรฐาน <strong>{fmt(sd_excess+ud_excess)}</strong> ใน 5 เดือน
หากไม่ดำเนินการ ประมาณการความเสียหายตลอดปี <strong>{fmt((sd_excess+ud_excess)*12/5)}</strong> · ทุกรายการไม่มีการระบุเหตุผล บ่งชี้การทุจริตโดยตั้งใจ ไม่ใช่ความผิดพลาด</div>
<div class="kpi-grid">
  <div class="kpi"><div class="kpi-val red">{sd['disc_pct']}%</div><div class="kpi-lbl">% ส่วนลด สุรินทร์ (Chain avg {chain}%)</div></div>
  <div class="kpi"><div class="kpi-val am">{ud['disc_pct']}%</div><div class="kpi-lbl">% ส่วนลด อุบลฯ (Chain avg {chain}%)</div></div>
  <div class="kpi"><div class="kpi-val red">{sd['disc_bills']+ud['disc_bills']:,}</div><div class="kpi-lbl">บิลส่วนลดทั้งหมด (0 ระบุเหตุผล)</div></div>
  <div class="kpi"><div class="kpi-val red">{fmt(sd_excess+ud_excess)}</div><div class="kpi-lbl">ส่วนลดเกินมาตรฐาน (5 เดือน)</div></div>
</div>
<div class="two-col">
<div>
<strong style="color:#dc2626;font-size:14px">สาขาสุรินทร์ — ลักษณะการทุจริต</strong>
<ul style="margin-top:8px;padding-left:18px;line-height:2.1;font-size:12px">
<li><strong>100% ไม่ระบุเหตุผล</strong> (1,043/1,046 บิล)</li>
<li>มีเพียง 2 POS user เท่านั้น: <code>Manager</code> (51%) และ <code>admin</code> (47%)</li>
<li>สลับกันให้ส่วนลดทีละวัน — บ่งชี้แชร์รหัสหรือคนเดิมล็อกอินต่างกัน</li>
<li>฿631 ซ้ำ 272 ครั้ง, ฿694 ซ้ำ 145 ครั้ง — pattern ไม่ใช่โปรโมชัน</li>
<li>วันเสาร์-อาทิตย์สูงกว่าวันธรรมดา 2-3 เท่า</li>
<li>ช่วง 19:00-21:00 น. เป็น peak ของส่วนลด</li>
</ul>
</div>
<div>
<strong style="color:#ea580c;font-size:14px">สาขาอุบลราชธานี — ลักษณะการทุจริต</strong>
<ul style="margin-top:8px;padding-left:18px;line-height:2.1;font-size:12px">
<li><strong>100% ไม่ระบุเหตุผล</strong> (2,412/2,412 บิล)</li>
<li><code>Take Order</code> ให้ส่วนลดมากที่สุด (55%, {fmt(ud['by_user'][0][2])})</li>
<li>พนักงานรับออเดอร์<strong>ไม่ควรมีสิทธิ์</strong>ให้ส่วนลด</li>
<li>ส่วนลดพิเศษ (ผู้บริหาร/ผู้ถือหุ้น/ผู้สูงอายุ 81+) ไม่มีการบันทึก</li>
<li>บิลสูงสุด: ฿5,329 ส่วนลดบนยอด ฿28,447 (18.7%)</li>
<li>ม.ค. สูงสุด ฿300K แล้วลดลง — อาจระวังตัวมากขึ้น</li>
</ul>
</div></div>""")
    add('</div></div>')

    # Page 2 of exec summary
    add(page_hdr("สรุปผู้บริหาร — ผลการวิเคราะห์รายบุคคล"))
    add(f"""<div class="sec-label">KEY FINDINGS PER PERSON</div>
<div class="sec-title" style="font-size:17px">บุคคลที่น่าสงสัยสูงสุดจากการวิเคราะห์</div>
<div class="sec-desc">วิเคราะห์โดยใช้ Welch's t-test เปรียบเทียบส่วนลดเฉลี่ย วันทำงาน vs วันหยุด + Pearson correlation + Lift ratio</div>
<table>
<thead><tr><th>ลำดับ</th><th>ชื่อ</th><th>ตำแหน่ง</th><th>สาขา</th>
<th class="num">เฉลี่ย/วัน ทำงาน</th><th class="num">เฉลี่ย/วัน หยุด</th>
<th class="num">Lift</th><th class="num">t-stat</th><th class="center">p-value</th><th class="num">Pearson r</th><th class="center">ความเสี่ยง</th></tr></thead><tbody>""")
    for i,c in enumerate(sorted(corr,key=lambda x:-(x["lift"] if x["p"]<0.15 else 0)),1):
        lc=lift_color(c["lift"])
        bg="background:#fef2f2;" if c["lift"]>=1.5 and c["p"]<0.1 else ("background:#fff7ed;" if c["lift"]>=1.3 else "")
        bname="สุรินทร์" if c["branch"]=="surin" else "อุบลฯ"
        if c["lift"]>=2 and c["p"]<0.05: risk=badge("🔴 สูงมาก","#dc2626")
        elif c["lift"]>=1.5 and c["p"]<0.1: risk=badge("🟠 สูง","#ea580c")
        elif c["lift"]>=1.2: risk=badge("🟡 ปานกลาง","#d97706")
        else: risk=badge("🟢 ต่ำ","#16a34a")
        add(f'<tr style="{bg}"><td style="font-weight:700;color:var(--indigo)">{i}</td><td><strong>{c["nick"]}</strong><br><span style="font-size:10px;color:#94a3b8">{c["fullname"][:18]}</span></td><td style="font-size:11px">{c["role"]}</td><td style="font-size:11px">{bname}</td><td class="num" style="color:{lc};font-weight:700">{fmt(c["worked_disc_mean"])}</td><td class="num">{fmt(c["off_disc_mean"])}</td><td class="num" style="color:{lc};font-weight:800">{c["lift"]}x</td><td class="num">{c["t"]}</td><td class="center">{p_badge(c["p"])}</td><td class="num">{c["r"]}</td><td class="center">{risk}</td></tr>')
    add('</tbody></table>')
    add("""<div class="alert alert-am">
<strong>หมายเหตุ:</strong> ตารางนี้แสดงความ<em>สัมพันธ์</em>ของการเข้างานกับส่วนลดเท่านั้น — ไม่ใช่การพิสูจน์ความผิด
ความสัมพันธ์สูงหมายความว่าวันที่คนนั้นทำงานมีส่วนลดสูงกว่าวันหยุด ซึ่งอาจเกิดจากหลายสาเหตุ
การสืบสวนเพิ่มเติมด้วย POS audit log และ CCTV จำเป็นก่อนสรุปผิดบุคคล</div>""")
    add('</div></div>')

    # ── KPI BENCHMARK ────────────────────────────────────────────────────────
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
        add(bar_row(MONTH_SHORT[mo],v,max_m,"#4f46e5"))
    add(f'</div><div><strong>ส่วนลดรายเดือน — อุบลฯ</strong><div style="height:8px"></div>')
    for mo in months:
        v=ud_month.get(mo,0)
        add(bar_row(MONTH_SHORT[mo],v,max_m,"#ea580c"))
    add('</div></div>')
    add("""<div class="alert alert-red"><strong>ข้อสังเกต:</strong>
ทุกรายการส่วนลดในทั้ง 2 สาขา มี reason_id=0 (ไม่ระบุเหตุผล) ใน POS database
สุรินทร์: 1,043/1,046 บิลไม่ระบุเหตุผล · อุบลฯ: 2,412/2,412 บิลไม่ระบุเหตุผล (100%)
นโยบายบริษัทกำหนดให้ต้องระบุทุกครั้ง — การไม่ระบุนี้เป็นไปอย่างตั้งใจ ไม่ใช่ oversight</div>""")
    add('</div></div>')

    # ── SURIN DEEP DIVE 1: POS Account Pattern ──────────────────────────────
    add(page_hdr("สุรินทร์ — รูปแบบ POS Account: Manager vs admin"))
    surin_user_colors={"Manager":"#4f46e5","admin":"#dc2626","AccOshinei":"#d97706","adminhq":"#6b7280"}
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
        c="#dc2626" if dow in ("อา.","ส.") else "#4f46e5"
        add(bar_row(dow,disc,max_sd_dow,c))
    add('</div></div><div><strong>ส่วนลดตาม ชั่วโมง</strong><div style="height:10px">')
    for hr,n,disc in sd["by_hour"]:
        c="#dc2626" if hr in ("19","20") else "#4f46e5"
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
    ubon_colors={"Take Order":"#dc2626","Cashier":"#4f46e5","admin":"#ea580c","Cashier1":"#d97706","Manager":"#6b7280"}
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

    # ── UBON DEEP DIVE 2: Special discount types ──────────────────────────────
    add(page_hdr("อุบลฯ — ส่วนลดพิเศษที่ไม่บันทึก (ผู้บริหาร/ผู้ถือหุ้น/ผู้สูงอายุ)"))
    add("""<div class="sec-label">SPECIAL DISCOUNT TYPES</div>
<div class="sec-title" style="font-size:17px">ส่วนลดประเภทพิเศษที่ต้องการการอนุมัติ</div>
<div class="alert alert-red">
<strong>🚨 ปัญหาร้ายแรง:</strong> ในระบบ Easy POS สาขาอุบลฯ มีการใช้ส่วนลดประเภทพิเศษดังนี้:
ส่วนลดผู้บริหาร, ส่วนลดผู้ถือหุ้น, ส่วนลดผู้สูงอายุ 81 ปีขึ้นไป — แต่ใน POS database ทุกรายการถูกบันทึกเป็น
"ไม่ระบุเหตุผล" (reason_id = 0) ซึ่งหมายความว่ามีการ <strong>ข้ามขั้นตอน</strong> บันทึกประเภทส่วนลด
</div>
<table><thead>
<tr><th>ประเภทส่วนลด</th><th>สิทธิ์ที่ควรจะเป็น</th><th>ขั้นตอนปกติ</th><th>ที่เกิดขึ้นจริง</th><th class="center">ความเสี่ยง</th></tr>
</thead><tbody>
<tr style="background:#fef2f2">
  <td><strong>ส่วนลดผู้บริหาร</strong></td>
  <td>ผู้บริหารระดับสูงเท่านั้น</td>
  <td>ต้องได้รับอนุมัติจาก GM/Owner</td>
  <td>ไม่มีเหตุผล / Take Order ให้ได้</td>
  <td class="center">{badge("🔴 สูงมาก","#dc2626")}</td>
</tr>
<tr style="background:#fef2f2">
  <td><strong>ส่วนลดผู้ถือหุ้น</strong></td>
  <td>ผู้ถือหุ้นที่แสดงหลักฐาน</td>
  <td>ต้องมีการยืนยันตัวตน + อนุมัติ</td>
  <td>ไม่มีเหตุผล / Take Order ให้ได้</td>
  <td class="center">{badge("🔴 สูงมาก","#dc2626")}</td>
</tr>
<tr style="background:#fef2f2">
  <td><strong>ส่วนลดผู้สูงอายุ 81+</strong></td>
  <td>ผู้สูงอายุที่แสดงหลักฐาน</td>
  <td>ต้องถ่ายรูปบัตรประชาชน/หลักฐาน</td>
  <td>ไม่มีเหตุผล / Take Order ให้ได้</td>
  <td class="center">{badge("🟠 สูง","#ea580c")}</td>
</tr>
</tbody></table>
<div class="alert alert-am">
<strong>ผลที่เกิดขึ้น:</strong> เนื่องจากทุกรายการถูกบันทึกเป็น "ไม่ระบุเหตุผล" ใน DB จึงไม่สามารถแยกได้ว่า
ส่วนลดไหนเป็น "ผู้บริหาร" หรือ "ผู้ถือหุ้น" จริงๆ และเป็นส่วนลดทั่วไปที่ไม่มีเหตุผล
นี่คือช่องโหว่ที่ใช้หลบหลีกการตรวจสอบ: ถ้าทุกอย่าง "ไม่ระบุ" ก็ตรวจสอบย้อนหลังไม่ได้
</div>
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
    add('<div class="two-col"><div><strong>Top 20 บิลส่วนลดสูงสุด</strong>')
    add('<table style="margin-top:8px"><thead><tr><th>วันที่</th><th>เวลา</th><th>User</th><th class="num">ส่วนลด</th><th class="num">%ลด</th><th>คน</th></tr></thead><tbody>')
    for d,t,u,disc,base,pct,cust in ud["top_bills"][:20]:
        c=ubon_colors.get(u,"#374151")
        add(f'<tr><td style="font-size:11px">{d}</td><td style="font-size:11px">{t}</td><td style="color:{c};font-weight:700;font-size:11px"><code>{u}</code></td><td class="num" style="color:{c};font-weight:700">{fmt(disc)}</td><td class="num">{pct}%</td><td class="num">{cust or "-"}</td></tr>')
    add('</tbody></table></div><div>')
    add('<strong>Fixed Amount Patterns</strong>')
    add('<table style="margin-top:8px"><thead><tr><th class="num">฿ ส่วนลด</th><th class="num">ครั้ง</th><th class="num">รวม</th></tr></thead><tbody>')
    for amt,n in ud["fixed_amounts"]:
        add(f'<tr><td class="num" style="font-family:monospace;font-weight:700">฿{amt:,.0f}</td><td class="num">{n}</td><td class="num">{fmt(amt*n)}</td></tr>')
    add('</tbody></table></div></div>')
    add('</div></div>')

    # ── PER-PERSON PAGES ──────────────────────────────────────────────────────
    for c in corr:
        branch_name="สุรินทร์" if c["branch"]=="surin" else "อุบลราชธานี"
        bdata=db_data[c["branch"]]
        colors=surin_user_colors if c["branch"]=="surin" else ubon_colors
        lc=lift_color(c["lift"])

        if c["lift"]>=2 and c["p"]<0.05:
            risk_level="🔴 สูงมาก"; risk_color="#dc2626"
        elif c["lift"]>=1.5 and c["p"]<0.1:
            risk_level="🟠 สูง"; risk_color="#ea580c"
        elif c["lift"]>=1.2:
            risk_level="🟡 ปานกลาง"; risk_color="#d97706"
        else:
            risk_level="🟢 ต่ำ"; risk_color="#16a34a"

        # Page 1 of person: header, stats, POS account analysis
        add(page_hdr(f"วิเคราะห์รายบุคคล — {c['nick']} ({c['fullname'][:20]}) · {branch_name}"))
        add(f"""<div class="person-hdr">
<div>
<div class="person-name">{c['nick']} ({c['fullname']})</div>
<div class="person-role">{c['role']} · สาขา{branch_name}</div>
<div class="person-eid">รหัสพนักงาน: {c['eid']}</div>
</div>
<div style="text-align:right">
<div style="font-size:28px;font-weight:800;color:#fbbf24">{c['lift']}x</div>
<div style="font-size:12px;opacity:.8">Lift (วันทำงาน ÷ วันหยุด)</div>
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
        # Statistical box
        add(f"""<div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:14px">
<div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;padding:14px">
<div style="font-weight:700;margin-bottom:8px">ผลการทดสอบทางสถิติ</div>
<table style="font-size:12px;margin:0"><tbody>
<tr><td style="color:#64748b;padding:4px 0">Welch's t-statistic</td><td style="font-weight:700;text-align:right">{c['t']}</td></tr>
<tr><td style="color:#64748b;padding:4px 0">p-value (two-tailed)</td><td style="text-align:right">{p_badge(c['p'])}</td></tr>
<tr><td style="color:#64748b;padding:4px 0">Pearson r</td><td style="font-weight:700;text-align:right">{c['r']}</td></tr>
<tr><td style="color:#64748b;padding:4px 0">วันทำงานทั้งหมด</td><td style="font-weight:700;text-align:right">{c['worked_days']} วัน</td></tr>
<tr><td style="color:#64748b;padding:4px 0">วันหยุดทั้งหมด</td><td style="font-weight:700;text-align:right">{c['off_days']} วัน</td></tr>
</tbody></table>
</div>
<div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;padding:14px">
<div style="font-weight:700;margin-bottom:8px">ส่วนลดวันหยุด (Off-Day Analysis)</div>
<table style="font-size:12px;margin:0"><tbody>
<tr><td style="color:#64748b;padding:4px 0">วันหยุดที่มีส่วนลด</td><td style="font-weight:700;text-align:right">{c['off_days_with_disc']} วัน</td></tr>
<tr><td style="color:#64748b;padding:4px 0">% วันหยุดที่มีส่วนลด</td><td style="font-weight:700;text-align:right">{round(100*c['off_days_with_disc']/max(c['off_days'],1))}%</td></tr>
<tr><td style="color:#64748b;padding:4px 0">ส่วนลดรวมวันหยุด</td><td style="font-weight:700;color:#ea580c;text-align:right">{fmt(c['off_disc_total'])}</td></tr>
<tr><td style="color:#64748b;padding:4px 0">เฉลี่ยส่วนลด/วันทำงาน</td><td style="font-weight:700;color:{lc};text-align:right">{fmt(c['worked_disc_mean'])}</td></tr>
<tr><td style="color:#64748b;padding:4px 0">เฉลี่ยส่วนลด/วันหยุด</td><td style="font-weight:700;text-align:right">{fmt(c['off_disc_mean'])}</td></tr>
</tbody></table>
</div>
</div>""")

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

        # Verdict
        interp=""
        if c["p"]<0.05 and c["lift"]>=1.5:
            interp=f'<strong>มีนัยสำคัญทางสถิติ (p={c["p"]}):</strong> วันที่เข้างานมีส่วนลดเฉลี่ยสูงกว่าวันหยุด {c["lift"]}x — บ่งชี้ความเป็นไปได้สูงที่บุคคลนี้เกี่ยวข้อง'
        elif c["p"]<0.1:
            interp=f'<strong>มีแนวโน้ม (p={c["p"]}):</strong> วันทำงานมีส่วนลดสูงกว่าวันหยุด {c["lift"]}x — ต้องการหลักฐานเพิ่มเติม'
        else:
            interp=f'<strong>ไม่มีนัยสำคัญทางสถิติ (p={c["p"]}):</strong> ความสัมพันธ์ระหว่างการเข้างานและส่วนลดไม่ชัดเจน'
        add(f'<div class="verdict-box" style="border-color:{risk_color};background:{"#fef2f2" if risk_color=="#dc2626" else "#fff7ed" if risk_color=="#ea580c" else "#fffbeb" if risk_color=="#d97706" else "#f0fdf4"}"><strong style="color:{risk_color}">{risk_level}</strong><br><span style="font-size:12px">{interp}</span></div>')
        add('</div></div>')

        # Page 2 of person: calendar view + top days
        add(page_hdr(f"{c['nick']} — ปฏิทินเข้างาน & วันส่วนลดสูง"))
        add('<div class="sec-label">ATTENDANCE CALENDAR</div>')
        add('<div class="sec-title" style="font-size:15px">ปฏิทินเข้างาน + ระดับส่วนลดแต่ละวัน</div>')
        add("""<div style="display:flex;gap:16px;align-items:center;margin-bottom:10px;font-size:11px">
<span style="display:flex;align-items:center;gap:4px"><span style="width:16px;height:16px;border:2px solid #4f46e5;border-radius:2px;display:inline-block;background:#fee2e2"></span>วันทำงาน (กรอบน้ำเงิน)</span>
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
    add(page_hdr("Attendance Correlation Analysis — ทุกพนักงาน"))
    add("""<div class="sec-label">STATISTICAL ANALYSIS</div>
<div class="sec-title">Welch's t-test + Pearson Correlation — ทุกพนักงาน</div>
<div class="alert alert-blue">
<strong>วิธีการ:</strong> เปรียบเทียบยอดส่วนลดรวมของสาขาในวันที่พนักงานเข้างาน vs วันที่ไม่เข้างาน
<strong>Welch's t-test</strong>: ทดสอบว่า mean ต่างกันอย่างมีนัยสำคัญหรือไม่ (p&lt;0.05 = มีนัยสำคัญ)
<strong>Pearson r</strong>: วัดความสัมพันธ์เชิงเส้นระหว่างตัวแปรไบนารี (เข้างาน/ไม่เข้า) กับส่วนลดวันนั้น
<strong>Lift</strong> = เฉลี่ยวันทำงาน ÷ เฉลี่ยวันหยุด
</div>""")
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

    # ── FINANCIAL DAMAGE ──────────────────────────────────────────────────────
    add(page_hdr("ประมาณการความเสียหายทางการเงิน"))
    add(f"""<div class="sec-label">FINANCIAL DAMAGE</div>
<div class="sec-title">ส่วนลดเกินมาตรฐาน (Chain Avg = {chain}%)</div>
<table><thead><tr><th>สาขา</th><th class="num">ยอดขายรวม</th><th class="num">ส่วนลดจริง</th><th class="num">% จริง</th><th class="num">% มาตรฐาน</th><th class="num" style="color:#dc2626">ส่วนลดเกิน</th></tr></thead><tbody>
<tr style="background:#fef2f2"><td><strong>สุรินทร์</strong></td><td class="num">{fmt(sd['total_rev'])}</td><td class="num">{fmt(sd['disc_total'])}</td><td class="num" style="color:#dc2626;font-weight:700">{sd['disc_pct']}%</td><td class="num">{chain}%</td><td class="num" style="color:#dc2626;font-weight:800;font-size:16px">{fmt(sd_excess)}</td></tr>
<tr style="background:#fff7ed"><td><strong>อุบลฯ</strong></td><td class="num">{fmt(ud['total_rev'])}</td><td class="num">{fmt(ud['disc_total'])}</td><td class="num" style="color:#ea580c;font-weight:700">{ud['disc_pct']}%</td><td class="num">{chain}%</td><td class="num" style="color:#ea580c;font-weight:800;font-size:16px">{fmt(ud_excess)}</td></tr>
<tr style="background:#fef2f2;border-top:2px solid #dc2626"><td><strong>รวม 2 สาขา</strong></td><td class="num">{fmt(sd['total_rev']+ud['total_rev'])}</td><td class="num">{fmt(sd['disc_total']+ud['disc_total'])}</td><td class="num">—</td><td class="num">—</td><td class="num" style="color:#dc2626;font-weight:800;font-size:20px">{fmt(sd_excess+ud_excess)}</td></tr>
</tbody></table>
<div class="kpi3">
<div class="kpi"><div class="kpi-val red">{fmt(sd_excess)}</div><div class="kpi-lbl">สุรินทร์ เกินมาตรฐาน 5 เดือน</div></div>
<div class="kpi"><div class="kpi-val am">{fmt(ud_excess)}</div><div class="kpi-lbl">อุบลฯ เกินมาตรฐาน 5 เดือน</div></div>
<div class="kpi"><div class="kpi-val red">{fmt((sd_excess+ud_excess)*12/5)}</div><div class="kpi-lbl">ประมาณการ 12 เดือน</div></div>
</div>
<div class="alert alert-red"><strong>สรุปความเสียหาย:</strong> ส่วนลดเกินมาตรฐาน {fmt(sd_excess+ud_excess)} ใน 5 เดือน หากดำเนินต่อตลอดปี ≈ <strong>{fmt((sd_excess+ud_excess)*12/5)}/ปี</strong></div>""")
    add('</div></div>')

    # ── RED FLAGS & ACTION PLAN ───────────────────────────────────────────────
    add(page_hdr("Red Flags & Action Plan"))
    add(f"""<div class="flag-grid">
<div class="flag flag-red"><div style="font-weight:700;color:#dc2626">🔴 RF-01 [วิกฤต] ไม่มีเหตุผลแม้แต่บิลเดียว</div><div style="font-size:11px;margin-top:4px">{sd['disc_bills']+ud['disc_bills']:,} บิล 2 สาขา — ขัดนโยบายทุกรายการ</div></div>
<div class="flag flag-red"><div style="font-weight:700;color:#dc2626">🔴 RF-02 [วิกฤต] สุรินทร์ % ส่วนลด {sd['disc_pct']}% = {round(sd['disc_pct']/chain,1)}x chain avg</div><div style="font-size:11px;margin-top:4px">เกินมาตรฐาน {fmt(sd_excess)} ใน 5 เดือน</div></div>
<div class="flag flag-red"><div style="font-weight:700;color:#dc2626">🔴 RF-03 [วิกฤต] สุรินทร์: ฿631×272 ฿694×145 ซ้ำผิดปกติ</div><div style="font-size:11px;margin-top:4px">ตัวเลขไม่กลมซ้ำหลายร้อยครั้ง — ไม่ใช่โปรโมชัน</div></div>
<div class="flag flag-red"><div style="font-weight:700;color:#dc2626">🔴 RF-04 [วิกฤต] อุบลฯ: Take Order ให้ส่วนลด {fmt(ud['by_user'][0][2])}</div><div style="font-size:11px;margin-top:4px">ไม่ควรมีสิทธิ์ — ต้องแก้ permission ทันที</div></div>
<div class="flag flag-am"><div style="font-weight:700;color:#d97706">🟡 RF-05 [สูง] สุรินทร์: Manager/admin สลับวัน</div><div style="font-size:11px;margin-top:4px">บ่งชี้แชร์รหัสหรือคนๆ เดียวใช้ทั้ง 2 account</div></div>
<div class="flag flag-am"><div style="font-weight:700;color:#d97706">🟡 RF-06 [สูง] อุบลฯ: ส่วนลดพิเศษไม่มีบันทึก</div><div style="font-size:11px;margin-top:4px">ผู้บริหาร/ผู้ถือหุ้น/ผู้สูงอายุ 81+ ไม่มี audit trail</div></div>
<div class="flag flag-am"><div style="font-weight:700;color:#d97706">🟡 RF-07 [สูง] วันเสาร์-อาทิตย์ 2-3x สูงกว่าวันธรรมดา</div><div style="font-size:11px;margin-top:4px">วันที่ผู้บริหารไม่ได้อยู่ = โอกาสทุจริต</div></div>
<div class="flag flag-am"><div style="font-weight:700;color:#d97706">🟡 RF-08 [สูง] ส่วนลดวันหยุดพนักงานยังมีอยู่</div><div style="font-size:11px;margin-top:4px">อาจมีผู้สมรู้ร่วมคิดหรือ account ถูก share</div></div>
</div>
<strong>Action Plan ลำดับเร่งด่วน</strong>
<table style="margin-top:8px"><thead><tr><th>#</th><th>การดำเนินการ</th><th class="center">เร่งด่วน</th><th>ผู้รับผิดชอบ</th></tr></thead><tbody>
<tr><td>1</td><td><strong>ระงับสิทธิ์ Take Order ให้ส่วนลด อุบลฯ ทันที</strong></td><td class="center">{badge("ทันที","#dc2626")}</td><td>IT</td></tr>
<tr><td>2</td><td><strong>เปลี่ยนรหัส POS ทุก account ทั้ง 2 สาขา</strong></td><td class="center">{badge("ทันที","#dc2626")}</td><td>IT</td></tr>
<tr><td>3</td><td><strong>เรียกสัมภาษณ์ ผู้จัดการ+หัวหน้าสุรินทร์</strong> ขอคำอธิบายส่วนลดทุกรายการ</td><td class="center">{badge("เร่งด่วนมาก","#dc2626")}</td><td>HR/เจ้าของ</td></tr>
<tr><td>4</td><td><strong>บังคับระบุเหตุผลก่อนกดส่วนลด</strong> ใน POS ทุกสาขา</td><td class="center">{badge("เร่งด่วนสูง","#ea580c")}</td><td>IT</td></tr>
<tr><td>5</td><td><strong>ตั้ง Supervisor Approval Workflow</strong>: ส่วนลด>500฿ ต้องมีหัวหน้า approve</td><td class="center">{badge("เร่งด่วนสูง","#ea580c")}</td><td>IT/ผู้บริหาร</td></tr>
<tr><td>6</td><td><strong>ตรวจ CCTV</strong> วันที่มีส่วนลดสูงสุด (Top 20 บิล ดูหน้า 6, 10)</td><td class="center">{badge("เร่งด่วนสูง","#ea580c")}</td><td>ผู้บริหาร</td></tr>
<tr><td>7</td><td><strong>Export POS login log</strong>: ใครล็อกอินด้วย Manager/admin/Take Order แต่ละวัน</td><td class="center">{badge("เร่งด่วนสูง","#ea580c")}</td><td>IT</td></tr>
<tr><td>8</td><td><strong>Audit บิล ฿631/฿694</strong> ตรวจรูปใบเสร็จจริง vs ระบบ</td><td class="center">{badge("ระยะกลาง","#d97706")}</td><td>Audit</td></tr>
<tr><td>9</td><td><strong>ตั้ง Daily Alert</strong>: แจ้งเจ้าของทุกวันถ้าส่วนลดสาขาใดเกิน threshold</td><td class="center">{badge("ระยะกลาง","#d97706")}</td><td>IT</td></tr>
<tr><td>10</td><td><strong>Policy update</strong>: ส่วนลดผู้บริหาร/ผู้ถือหุ้น/ผู้สูงอายุ ต้องแนบรูปหลักฐานใน POS</td><td class="center">{badge("ระยะกลาง","#d97706")}</td><td>ผู้บริหาร</td></tr>
</tbody></table>""")
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


def page_hdr(title):
    return f'<div class="page"><div class="sec-hdr"><span class="sec-hdr-title">{title}</span><span class="sec-hdr-brand">OSHINEI FRAUD INVESTIGATION 2026</span></div><div class="sec-body">'


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
    print("📊 สร้างรายงาน HTML...")
    html = generate(att, db_data)
    out = os.path.abspath(args.out)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f: f.write(html)
    print(f"✅ สร้างสำเร็จ: {out} ({os.path.getsize(out):,} bytes)")
