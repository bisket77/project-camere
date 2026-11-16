@echo off
chcp 65001 >nul
rem สั่งเว็บบน PC (ที่รันด้วย tools\run_local.ps1) ให้ดึง Google Sheet ทันที
rem ใช้: ดับเบิลคลิกไฟล์นี้  หรือพิมพ์  tools\sync_PC.bat  ใน cmd / PowerShell
cd /d "%~dp0.."
if not exist "web\.env.b" (
  echo [X] ยังไม่มีไฟล์ web\.env.b ^(SHEET_URL / SHEET_TOKEN^) - เว็บบน PC ไม่ได้เชื่อม Sheet
  goto :end
)
if not exist "web\data" mkdir "web\data"
type nul > "web\data\.sync_now"
echo สั่ง sync แล้ว - รอเว็บรับคำสั่ง...
for /l %%i in (1,1,10) do (
  if not exist "web\data\.sync_now" goto :ok
  timeout /t 1 /nobreak >nul
)
echo [!] เว็บยังไม่รับคำสั่งใน 10 วินาที
echo     - เปิดเว็บด้วย: powershell -ExecutionPolicy Bypass -File tools\run_local.ps1
echo     - ดูว่าในหน้าต่างเว็บมี "[sheet] เปิดซิงก์กับ Google Sheet"
goto :end
:ok
echo [OK] เว็บรับคำสั่งแล้ว - ดูผลในหน้าต่างเว็บ: [sheet] ได้ข้อมูลใหม่ ... แถว
:end
echo.
pause
