<#
รันเว็บบน PC (Windows) แบบเดียวกับที่ NUC-02 รัน — ไว้แก้/ทดสอบก่อนส่งเข้า Linux
  - ใช้ DB เดียวกับที่จะส่งไป NUC-02: web\data\DB_NUG02.db  (เปลี่ยนด้วย -Db)
  - ดึง Google Sheet ด้วยค่าใน web\.env.b (SHEET_URL / SHEET_TOKEN) ถ้ามีไฟล์

ตัวอย่าง (cmd ที่โฟลเดอร์โปรเจกต์):
  powershell -ExecutionPolicy Bypass -File tools\run_local.ps1               # http://127.0.0.1:5000
  powershell -ExecutionPolicy Bypass -File tools\run_local.ps1 -Port 5050    # พอร์ตอื่น
  powershell -ExecutionPolicy Bypass -File tools\run_local.ps1 -Db cameras.db  # ใช้ DB เดิมของ PC
  powershell -ExecutionPolicy Bypass -File tools\run_local.ps1 -NoSheet     # ไม่ดึง Sheet
หยุด: Ctrl+C
#>
param(
    [string]$Db = 'DB_NUG02.db',
    [int]$Port = 5000,
    [string]$EnvFile,
    [switch]$NoSheet
)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root
if (-not $EnvFile) { $EnvFile = Join-Path $root 'web\.env.b' }

# ค่าจากไฟล์ .env (บรรทัด KEY=VALUE) -> ตัวแปรสภาพแวดล้อมของรอบนี้
if (-not $NoSheet -and (Test-Path $EnvFile)) {
    foreach ($line in Get-Content -Encoding UTF8 $EnvFile) {
        if ($line -match '^\s*([A-Z_][A-Z0-9_]*)\s*=\s*(.*)$' -and $Matches[1] -ne 'CAMERA_DB_PATH') {
            Set-Item "env:$($Matches[1])" $Matches[2].Trim()
        }
    }
    Write-Host "ค่า Sheet จาก $EnvFile"
} else {
    Remove-Item env:SHEET_URL, env:SHEET_TOKEN -ErrorAction SilentlyContinue
    Write-Host "ไม่ดึง Google Sheet $(if (-not $NoSheet) { "(ไม่มีไฟล์ $EnvFile)" })"
}

$env:CAMERA_DB_PATH = Join-Path $root "web\data\$Db"
$env:PYTHONIOENCODING = 'utf-8'
Write-Host "DB : $env:CAMERA_DB_PATH$(if (-not (Test-Path $env:CAMERA_DB_PATH)) { '  (ยังไม่มี จะสร้างใหม่ว่าง ๆ)' })"
Write-Host "เว็บ: http://127.0.0.1:$Port    หยุด: Ctrl+C"
Write-Host "สั่งซิงก์ Sheet ทันที: ดับเบิลคลิก tools\sync_PC.bat  (หรือพิมพ์ tools\sync_PC.bat ในหน้าต่างอื่น)"
Write-Host ''

$py = if (Test-Path (Join-Path $root '.venv\Scripts\python.exe')) { Join-Path $root '.venv\Scripts\python.exe' } else { 'python' }
& $py web\app.py --port $Port
