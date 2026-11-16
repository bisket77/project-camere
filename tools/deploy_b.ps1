<#
ส่งโค้ดไปเครื่องในวง ZeroTier B (NUC-02 / งานลูกค้า) — แยกจาก NUC-10 (วง A) โดยสิ้นเชิง
ข้อมูลแยกกัน: NUC-02 ใช้ DB ชื่อ DB_NUG02.db (ไม่ใช่ cameras.db ของ NUC-10) — -WithDb ส่ง web\data\DB_NUG02.db จาก PC
DB เดิมบน NUC-02 ถูกสำรองไว้ที่ ~/db-backup ทุกครั้งก่อนทับ (ถ้าใช้ -WithDb)

ตัวอย่าง (รันใน cmd ที่โฟลเดอร์โปรเจกต์):
  powershell -ExecutionPolicy Bypass -File tools\deploy_b.ps1            # ส่งเฉพาะโค้ด
  powershell -ExecutionPolicy Bypass -File tools\deploy_b.ps1 -WithDb    # ส่งโค้ด + DB จาก PC (ทับของ NUC-02)
  ถ้ามีไฟล์ web\.env.b (SHEET_URL / SHEET_TOKEN) บน PC จะส่งไปเป็น web/.env ให้ด้วย -> เว็บดึงกล้องจาก Sheet เอง
  powershell -ExecutionPolicy Bypass -File tools\deploy_b.ps1 -Check     # แค่เช็กว่าเว็บ/SSH ของวง B ติดไหม
  powershell -ExecutionPolicy Bypass -File tools\deploy_b.ps1 -Target b02@172.28.x.x   # เครื่องอื่นในวง B
#>
param(
    [string]$Target = $(if ($env:NUC_B_TARGET) { $env:NUC_B_TARGET } else { 'b02@172.28.107.192' }),
    [string]$Domain = 'cctv.sut',
    # DB ของวง B แยกจาก cameras.db: บน PC อยู่ที่ web\data\DB_NUG02.db, บน NUC-02 เว็บใช้ไฟล์ชื่อเดียวกัน
    [string]$DbName = 'DB_NUG02.db',
    [switch]$WithDb,
    [switch]$DryRun,
    [switch]$Check,
    # ค่าซิงก์ Google Sheet ของวง B (ไฟล์อยู่บน PC, ไม่ขึ้น GitHub) — มีไฟล์นี้เมื่อไหร่ ส่งไปเป็น web/.env ให้เอง
    [string]$EnvFile = (Join-Path (Split-Path $PSScriptRoot -Parent) 'web\.env.b')
)
$ErrorActionPreference = 'Stop'
$ip = ($Target -split '@')[-1]
$envArg = @{}
if (Test-Path $EnvFile) { $envArg.EnvFile = $EnvFile }
else { Write-Host "(ไม่มี $EnvFile — web/.env บน NUC-02 คงเดิม)" }

if ($Check) {
    & (Join-Path $PSScriptRoot 'open_nuc.ps1') -Check -Hosts @($Domain, $ip)
    & (Join-Path $PSScriptRoot 'open_nuc.ps1') -Check -Ssh -Hosts @($ip)
    return
}

Write-Host "== วง B: ส่งไป $Target $(if ($WithDb) { '(โค้ด + DB)' } else { '(เฉพาะโค้ด, DB ของ NUC-02 ไม่ถูกแตะ)' }) =="
& (Join-Path $PSScriptRoot 'deploy_nuc.ps1') -Target $Target -NoDb:(-not $WithDb) -DryRun:$DryRun -DbName $DbName @envArg
if (-not $DryRun) { Write-Host "เปิดเว็บ: http://$ip   หรือ   http://$Domain (ถ้าตั้ง DNS วง B แล้ว)" }
