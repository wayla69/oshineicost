# -*- coding: utf-8 -*-
"""Build the PRINT edition of the customer-feedback go-live checklist.

The plan the dashboard tab renders (backend/checklists/feedback_rollout.json,
served by /api/checklist/feedback_rollout/plan) is the single source; it is
inlined into a print template designed for paper: one column, every step an
unbreakable unit, a compact type scale, and pastel highlights that mark what
matters (commands and files, why, done-when, if-stuck, warnings).

    py scripts/build_feedback_checklist_pdf.py            # → docs/FEEDBACK_ROLLOUT_CHECKLIST_PRINT.html + .pdf
    py scripts/build_feedback_checklist_pdf.py --no-pdf   # HTML only
"""
import argparse
import io
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLAN_JSON = ROOT / "backend" / "checklists" / "feedback_rollout.json"
OUT_HTML = ROOT / "docs" / "FEEDBACK_ROLLOUT_CHECKLIST_PRINT.html"
OUT_PDF = ROOT / "docs" / "FEEDBACK_ROLLOUT_CHECKLIST.pdf"
CHROME = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")

TEMPLATE = r"""<!doctype html>
<html lang="th">
<head>
<meta charset="utf-8">
<title>Checklist เปิดใช้งานเสียงลูกค้า — ฉบับพิมพ์</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Bai+Jamjuree:wght@600;700&family=Sarabun:wght@400;500;700&family=IBM+Plex+Mono:wght@500&display=swap">
<style>
  @page { size: A4; margin: 11mm 11mm 13mm 11mm;
    @bottom-left { content: "Oshinei · Checklist เปิดใช้งานเสียงลูกค้า · ฉบับพิมพ์"; font: 7.5pt "Sarabun", sans-serif; color: #7a8681; }
    @bottom-right { content: "หน้า " counter(page) " / " counter(pages); font: 7.5pt "Sarabun", sans-serif; color: #7a8681; } }
  * { box-sizing: border-box; }
  html, body { margin: 0; padding: 0; background: #fff; }
  body { font: 9.3pt/1.36 "Sarabun", "Segoe UI", Tahoma, sans-serif; color: #1b232a; }
  h1, h2, h3 { font-family: "Bai Jamjuree", "Sarabun", sans-serif; margin: 0; }
  code, .cmd { font-family: "IBM Plex Mono", Consolas, monospace; }
  /* pastel palette: teal band, blue why, green done, rose stuck, yellow command, peach warning, lilac value */
  .cmd { background: #fff1bf; color: #4a3a00; padding: 0 2.5pt; border-radius: 2.5pt; font-size: 8.3pt; overflow-wrap: anywhere; word-break: break-all; }
  mark.warn { background: #ffd8c4; color: #7a2b0c; padding: 0 2.5pt; border-radius: 2.5pt; font-weight: 700; }
  .val { background: #e8e1f7; color: #3b2a6b; padding: 0 2.5pt; border-radius: 2.5pt; font-weight: 700; }
  /* cover */
  .cover { margin-bottom: 4mm; }          /* the phases follow on the same page: no blank third of a sheet */
  .brand { font: 700 7.5pt "Bai Jamjuree"; letter-spacing: .12em; text-transform: uppercase; color: #0f766e; }
  .cover h1 { font-size: 22pt; line-height: 1.15; margin: 3pt 0 4pt; }
  .lede { font-size: 10.2pt; color: #46535e; max-width: 165mm; margin: 0 0 6pt; }
  .cover-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 6mm; margin-top: 4mm; }
  .box { border: .6pt solid #c9d4cf; border-radius: 4pt; padding: 5pt 7pt; }
  .box h3 { font-size: 10pt; color: #0b5e58; margin-bottom: 3pt; }
  .box ul { margin: 0; padding-left: 12pt; }
  .box li { margin: 1pt 0; }
  table.sum { border-collapse: collapse; width: 100%; font-size: 8.6pt; margin-top: 4mm; }
  table.sum th { text-align: left; font: 700 7.6pt "Bai Jamjuree"; letter-spacing: .05em; color: #556; background: #eef3f0; padding: 3pt 5pt; border-bottom: .8pt solid #b9c6c0; }
  table.sum td { padding: 2.5pt 5pt; border-bottom: .5pt solid #dfe6e2; vertical-align: top; }
  table.sum td.n { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
  .gantt { margin-top: 4mm; border: .6pt solid #c9d4cf; border-radius: 4pt; padding: 4pt 6pt; }
  .gantt svg { width: 100%; height: auto; display: block; }
  .legend { display: flex; flex-wrap: wrap; gap: 3pt 10pt; font-size: 8pt; color: #46535e; margin-top: 3pt; }
  .legend span::before { content: ""; display: inline-block; width: 11pt; height: 7pt; border-radius: 1.5pt; margin-right: 3pt; vertical-align: -.5pt; }
  .l-plan::before { background: #a9d9d0; } .l-wait::before { background: repeating-linear-gradient(45deg, #e0a56a 0 2pt, transparent 2pt 4pt); border: .6pt solid #e0a56a; } .l-today::before { border-top: 1.2pt dashed #b91c1c; height: 0; }
  .keys { display: flex; flex-wrap: wrap; gap: 4pt 8pt; font-size: 8.4pt; margin-top: 4mm; }
  /* body: one column, full width; pagination rules keep every unit whole:
     a phase starts a new page and sticks to its first task, a task header sticks to
     its first step, a step never splits across pages */
  .phase { break-after: avoid; break-inside: avoid; margin: 5mm 0 2.5mm; background: #d9efe9; border-left: 4pt solid #0f766e; padding: 3.5pt 7pt; }
  .phase:first-child { margin-top: 0; }
  .phase h2 { font-size: 12.5pt; line-height: 1.2; }
  .phase .meta { font-size: 8.2pt; color: #3a4a45; margin-top: 1pt; }
  .phase .why { font-size: 8.9pt; color: #22322d; margin-top: 2pt; }
  .task { border: .6pt solid #c9d4cf; border-radius: 3pt; margin: 0 0 3mm; break-inside: auto; }
  .task-h { background: #eaf0f4; padding: 3pt 7pt; border-bottom: .6pt solid #c9d4cf; break-inside: avoid; break-after: avoid; display: grid; grid-template-columns: 1fr auto; gap: 2pt 8pt; align-items: baseline; }
  .task-h h3 { font-size: 10.2pt; line-height: 1.25; }
  .task-h .est { font-weight: 400; color: #556; font-size: 8.4pt; }
  .task-h .status { font-size: 8pt; color: #3a4a45; display: flex; flex-wrap: wrap; gap: 2pt 7pt; white-space: nowrap; }
  .task-h .status i { font-style: normal; display: inline-block; width: 8pt; height: 8pt; border: .8pt solid #556; border-radius: 1.5pt; vertical-align: -1pt; margin-right: 2pt; }
  .note { font-size: 8pt; color: #7a8681; padding: 2pt 7pt 3pt; border-bottom: .5pt dashed #d6ded9; break-inside: avoid; break-after: avoid; display: grid; grid-template-columns: auto 1fr; gap: 6pt; align-items: end; }
  .note span { display: block; border-bottom: .5pt solid #c9d4cf; height: 9pt; }
  .step { padding: 3pt 7pt 4pt; border-top: .5pt solid #e1e7e3; break-inside: avoid; }
  .step:first-of-type { border-top: 0; }
  .step .t { display: grid; grid-template-columns: 10pt 1fr; gap: 3pt; font-weight: 700; font-size: 9.6pt; }
  .step .t i { display: inline-block; width: 8.5pt; height: 8.5pt; border: 1pt solid #333; border-radius: 1.5pt; margin-top: 2pt; }
  .body { display: grid; grid-template-columns: 1.4fr 1fr; gap: 2pt 7pt; margin: 2pt 0 0 13pt; font-size: 8.9pt; align-items: start; }
  .body.nohow { grid-template-columns: 1fr; }
  .body b { display: block; font: 700 7pt "Bai Jamjuree"; letter-spacing: .05em; text-transform: uppercase; color: #6b7680; margin-bottom: 1pt; }
  .body ol { margin: 0; padding-left: 11pt; }
  .body ol li { margin: .6pt 0; }
  .side > div { padding: 2pt 5pt 3pt; border-radius: 2.5pt; margin-bottom: 2.5pt; }
  .side > div:last-child { margin-bottom: 0; }
  .side .why { background: #e7effa; }
  .side .check { background: #dff3e6; color: #14532d; }
  .side .stuck { background: #fbe4e4; color: #7f1d1d; }
  .side .check b { color: #2f6b45; } .side .stuck b { color: #9b3b3b; } .side .why b { color: #4a6591; }
</style>
</head>
<body>
<section class="cover">
  <div class="brand">Oshinei Dashboard · โมดูล 💬 เสียงลูกค้า</div>
  <h1>Checklist เปิดใช้งานเสียงลูกค้าให้ครบทุกช่องทาง</h1>
  <p class="lede">คู่มือแบบจับมือทำ ทีละคลิก: ตั้งค่าแต่ละแพลตฟอร์ม ทดสอบ วางกระบวนการประจำ และตรวจรับ พิมพ์ฉบับนี้ไว้ทำงานคู่กับหน้า interactive (สถานะติ๊กร่วมกันทั้งทีม) — ทุกขั้นตอนบอกว่าทำอย่างไร ทำไม สำเร็จเมื่อไร และถ้าติดทำอย่างไร</p>
  <div class="cover-grid">
    <div class="box"><h3>วิธีใช้เอกสารนี้</h3><ul>
      <li>ทำตามลำดับเฟส 0 ก่อน จากนั้นเฟส 1, 2, 4, 6 ทำคู่ขนานได้ในสัปดาห์แรก เฟส 1 และ 3 มีช่วงรออนุมัติ ยื่นให้เร็วแล้วไปทำเฟสอื่น</li>
      <li>ทุกงานมีช่องสถานะ 4 ช่องและบรรทัดหมายเหตุ ติ๊กและจดบนกระดาษ แล้วไปติ๊กในหน้า interactive เพื่อให้ทีมเห็นสด</li>
      <li>คำสั่งทั้งหมดพิมพ์ในหน้าต่าง Command Prompt ที่โฟลเดอร์ <span class="cmd">C:\Users\ASUS\Oshinei</span> บนเครื่อง sync</li>
      <li>ไฟล์ตั้งค่า <span class="cmd">backend\.feedback_config.json</span> และ <span class="cmd">backend\.env</span> เป็นความลับ แก้บนเครื่องนั้นเท่านั้น ห้ามส่งทางแชท</li>
    </ul></div>
    <div class="box"><h3>สัญลักษณ์และสีที่ใช้</h3><ul>
      <li><span class="cmd">สีเหลือง</span> = คำสั่ง ชื่อไฟล์ ลิงก์ ที่ต้องพิมพ์หรือเปิดให้ตรงทุกตัวอักษร</li>
      <li><span style="background:#e7effa;padding:0 3pt;border-radius:2.5pt">ฟ้า = ทำไมต้องทำ</span> &nbsp; <span style="background:#dff3e6;color:#14532d;padding:0 3pt;border-radius:2.5pt">เขียว = รู้ว่าสำเร็จเมื่อ</span> &nbsp; <span style="background:#fbe4e4;color:#7f1d1d;padding:0 3pt;border-radius:2.5pt">ชมพู = ถ้าติดให้ทำอย่างนี้</span></li>
      <li><mark class="warn">พีช</mark> = ข้อควรระวัง ห้าม หรือจุดที่พลาดแล้วเสียเวลามาก &nbsp; <span class="val">ม่วง</span> = ค่า/ตัวเลขที่ต้องจด</li>
      <li>ช่อง ☐ ข้างขั้นตอน = ติ๊กเมื่อทำเสร็จ · แถบลายในไทม์ไลน์ = รอฝ่ายภายนอกอนุมัติ</li>
    </ul></div>
  </div>
  <table class="sum" id="sumTable"></table>
  <div class="gantt"><div id="ganttHost"></div>
    <div class="legend"><span class="l-plan">ช่วงทำงานตามแผน</span><span class="l-wait">รอ Google / TikTok อนุมัติ</span><span class="l-today">วันที่พิมพ์</span><span id="ganttNote"></span></div></div>
</section>
<div id="phases"></div>
<script>
__PLAN__
/* ── highlight rules (pastel) ── */
function esc(s){ return String(s == null ? "" : s).replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c])); }
const WARN = [/ห้าม[^\s,·]*(?:\s[^\s,·]+){0,3}/g, /สำคัญมาก/g, /อย่าลืม/g, /ไม่มีย้อนหลัง/g, /หมดอายุทุก 7 วัน/g, /ไม่หมดอายุ/g, /ทางวิกฤต[^\s,·]*/g, /เก็บเป็นความลับ/g, /จะเห็นครั้งเดียว/g, /ต้องผ่านหลัง[^\s,·]*(?:\s[^\s,·]+){0,3}/g, /ค่าใช้จ่าย[^\s,·]*/g];
const VAL = [/~?\$\d[\d,]*(?:\/เดือน)?/g, /\d+[–-]\d+ สัปดาห์/g, /\d+ (?:สัปดาห์|วัน|นาที|ชั่วโมง)/g, /≤ ?\d+ ตัวอักษร|ไม่เกิน \d+ ตัวอักษร/g, /-100…/g, /09:00|07:30/g];
function hl(s){
  let t = esc(s);
  t = t.replace(/(https?:\/\/[^\s"<>)]+)/g, '<span class="cmd">$1</span>');
  t = t.replace(/(py backend\\fetch_feedback\.py[^→(]*?)(?=\s*(?:→|\(|ต้อง|ดู|แล้ว|กด|ใส่|$))/g, (m, c) => '<span class="cmd">' + c.trim() + '</span>');
  t = t.replace(/(flyctl secrets (?:set|list)[^→(]*?)(?=\s*(?:→|\(|$))/g, '<span class="cmd">$1</span>');
  t = t.replace(/(?<![\w\/">])((?:C:\\|backend\\|OneDrive\\)[^\s"&,()]+(?:\s[A-Za-z]+\.(?:bat|log|txt|json))?)/g, '<span class="cmd">$1</span>');
  t = t.replace(/(?<![\w\/">])([\w.-]+\.(?:json|env|bat|log|txt|zip|py|html|pdf)\b)/g, (m, f) => m.includes("cmd") ? m : '<span class="cmd">' + f + '</span>');
  WARN.forEach(re => { t = t.replace(re, m => '<mark class="warn">' + m + '</mark>'); });
  VAL.forEach(re => { t = t.replace(re, m => '<span class="val">' + m + '</span>'); });
  return t;
}
/* ── render ── */
const statusBoxes = Object.values(STATUS).map(v => `<span><i></i>${v}</span>`).join("");
let nSteps = 0, nTasks = 0;
document.getElementById("phases").innerHTML = PLAN.map(ph => {
  const steps = ph.tasks.reduce((a, t) => a + t.steps.length, 0); nSteps += steps; nTasks += ph.tasks.length;
  return `<div class="phase"><h2>${esc(ph.name)}</h2><div class="meta">ผู้รับผิดชอบ: ${esc(ph.owner)} · วันที่ ${ph.days[0]+1}–${ph.days[1]} ของโครงการ${ph.wait ? " (รวมช่วงรออนุมัติ)" : ""} · ${ph.tasks.length} งาน ${steps} ขั้นตอน</div><div class="why">${hl(ph.why)}</div></div>` +
    ph.tasks.map(t => `<div class="task"><div class="task-h"><h3>${esc(t.name)} <span class="est">· ${esc(t.est)}</span></h3><div class="status">สถานะ: ${statusBoxes} <span>ผู้ทำ ________ วันที่ ______</span></div></div><div class="note">หมายเหตุ<span></span></div>` +
      t.steps.map(s => {
        const hasHow = s.how && s.how.length;
        const side = (s.why ? `<div class="why"><b>ทำไม</b>${hl(s.why)}</div>` : "") +
                     (s.check ? `<div class="check"><b>สำเร็จเมื่อ</b>${hl(s.check)}</div>` : "") +
                     (s.stuck ? `<div class="stuck"><b>ถ้าติด</b>${hl(s.stuck)}</div>` : "");
        return `<div class="step"><div class="t"><i></i><span>${hl(s.t)}</span></div><div class="body${hasHow ? "" : " nohow"}">` +
          (hasHow ? `<div class="how"><b>ทำอย่างไร</b><ol>${s.how.map(h => `<li>${hl(h)}</li>`).join("")}</ol></div>` : "") +
          (side ? `<div class="side">${side}</div>` : "") + `</div></div>`;
      }).join("") + `</div>`).join("");
}).join("");
/* summary table */
document.getElementById("sumTable").innerHTML = `<thead><tr><th>เฟส</th><th>ผู้รับผิดชอบ</th><th>ช่วง (วันของโครงการ)</th><th>งาน</th><th>ขั้นตอน</th><th>รอภายนอก</th></tr></thead><tbody>` +
  PLAN.map(ph => `<tr><td>${esc(ph.name)}</td><td>${esc(ph.owner)}</td><td class="n">${ph.days[0]+1}–${ph.days[1]}</td><td class="n">${ph.tasks.length}</td><td class="n">${ph.tasks.reduce((a,t)=>a+t.steps.length,0)}</td><td>${ph.wait ? `วันที่ ${ph.wait[0]+1}–${ph.wait[1]}` : "—"}</td></tr>`).join("") +
  `<tr><td colspan="3"><b>รวม</b></td><td class="n"><b>${nTasks}</b></td><td class="n"><b>${nSteps}</b></td><td></td></tr></tbody>`;
/* gantt (static, planned bars) */
(function(){
  const startIso = "__START__"; const start = new Date(startIso + "T00:00:00");
  const addDays = n => { const d = new Date(start); d.setDate(d.getDate() + n); return d; };
  const th = d => d.toLocaleDateString("th-TH", {day:"numeric", month:"short"});
  const left = 150, dayW = 13, rowH = 17, top = 20, W = left + HORIZON * dayW + 30, H = top + PLAN.length * rowH + 12;
  const today = new Date(); today.setHours(0,0,0,0); const td = Math.round((today - start) / 86400000);
  let g = `<svg viewBox="0 0 ${W} ${H}" xmlns="http://www.w3.org/2000/svg" font-family="Sarabun, sans-serif"><defs><pattern id="h" width="4" height="4" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><rect width="2" height="4" fill="#e0a56a"/></pattern></defs>`;
  for (let d = 0; d <= HORIZON; d += 7) { const x = left + d * dayW; g += `<line x1="${x}" y1="${top-4}" x2="${x}" y2="${H-4}" stroke="#dfe6e2"/><text x="${x+2}" y="${top-8}" font-size="7.5" fill="#6b7680">${th(addDays(d))}</text>`; }
  PLAN.forEach((ph, i) => { const y = top + i * rowH, [a, b] = ph.days, x1 = left + a * dayW, w = Math.max(dayW * .6, (b - a) * dayW);
    g += `<text x="4" y="${y+12}" font-size="8" font-weight="700" fill="#1b232a">${esc(ph.name.split(" · ")[0])}</text><text x="38" y="${y+12}" font-size="7.6" fill="#46535e">${esc((ph.name.split(" · ")[1]||"").slice(0,26))}</text>`;
    g += `<rect x="${x1}" y="${y+3}" width="${w}" height="11" rx="2.5" fill="#a9d9d0"/>`;
    if (ph.wait) g += `<rect x="${left + ph.wait[0]*dayW}" y="${y+3}" width="${(ph.wait[1]-ph.wait[0])*dayW}" height="11" rx="2.5" fill="url(#h)"/>`;
    g += `<text x="${x1+w+4}" y="${y+12}" font-size="7.4" fill="#46535e">วัน ${a+1}–${b}</text>`; });
  if (td >= 0 && td <= HORIZON) { const x = left + td * dayW; g += `<line x1="${x}" y1="${top-4}" x2="${x}" y2="${H-4}" stroke="#b91c1c" stroke-width="1" stroke-dasharray="3 2"/>`; }
  g += `</svg>`;
  document.getElementById("ganttHost").innerHTML = g;
  document.getElementById("ganttNote").textContent = `วันเริ่มโครงการตามแผน ${th(start)} · พิมพ์เมื่อ ${th(today)} ${today.getFullYear()+543}`;
})();
</script>
</body>
</html>
"""


def build(no_pdf: bool = False, start: str = "") -> int:
    """The plan is the server's JSON (backend/checklists/feedback_rollout.json —
    what the dashboard tab renders); it is inlined as JS constants."""
    plan = json.loads(io.open(PLAN_JSON, encoding="utf-8").read())
    start = start or plan.get("default_start") or "2026-09-07"
    consts = ("const PLAN = " + json.dumps(plan["phases"], ensure_ascii=False) + ";\n"
              "const STATUS = " + json.dumps(plan["statuses"], ensure_ascii=False) + ";\n"
              f"const HORIZON = {int(plan.get('horizon_days') or 36)};\n")
    page = TEMPLATE.replace("__PLAN__", consts).replace("__START__", start)
    io.open(OUT_HTML, "w", encoding="utf-8").write(page)
    print(f"html → {OUT_HTML.relative_to(ROOT)} ({len(page)/1024:.0f} KB)")
    if no_pdf:
        return 0
    if not CHROME.exists():
        sys.exit("Chrome not found — HTML only")
    cmd = [str(CHROME), "--headless=new", "--disable-gpu", "--no-pdf-header-footer", "--virtual-time-budget=10000",
           f"--print-to-pdf={OUT_PDF}", OUT_HTML.resolve().as_uri()]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=240)
    if r.returncode or not OUT_PDF.exists():
        sys.exit(f"chrome failed ({r.returncode}): {r.stderr[-400:]}")
    try:
        import pikepdf
        with pikepdf.open(str(OUT_PDF)) as p:
            pages = len(p.pages)
    except Exception:
        pages = "?"
    print(f"pdf  → {OUT_PDF.relative_to(ROOT)} ({OUT_PDF.stat().st_size/1024:.0f} KB, {pages} pages)")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-pdf", action="store_true")
    ap.add_argument("--start", default="", help="project start date drawn on the cover Gantt (default: the plan's)")
    a = ap.parse_args()
    sys.exit(build(a.no_pdf, a.start))
