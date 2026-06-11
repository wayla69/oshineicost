@echo off
rem เปิดระบบกระทบยอด (recon) + หน้า v2 ในเบราว์เซอร์
rem ปิดหน้าต่างดำนี้ = ปิดเซิร์ฟเวอร์
cd /d "%~dp0"
set OSHINEI_DB=backend/oshinei.db
start "" "http://127.0.0.1:8014/app/v2.html"
python -m uvicorn recon.main:app --port 8014
pause
