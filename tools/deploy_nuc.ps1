<#
ส่งโค้ด (และ DB) จาก PC ไปเครื่องเซิร์ฟเวอร์ (NUC) ด้วยคำสั่งเดียว
ใช้ ssh / scp / tar ที่มากับ Windows 10/11 อยู่แล้ว ไม่ต้องลงอะไรเพิ่ม

ตัวอย่าง:
  .\tools\deploy_nuc.ps1                         # ส่งโค้ด + DB ไป NUC-10
  .\tools\deploy_nuc.ps1 -NoDb                   # ส่งเฉพาะโค้ด (DB บนเซิร์ฟเวอร์ไม่ถูกแตะ)
  .\tools\deploy_nuc.ps1 -Target bis@<IP อื่น>   # ส่งไปเครื่องอื่น
  .\tools\deploy_nuc.ps1 -DryRun                 # สร้างไฟล์แพ็กอย่างเดียว ไม่ส่ง

สิ่งที่ส่ง : web/ tools/ tests/ requirements*.txt pyproject.toml README.md
ไม่ส่ง    : .venv .git __pycache__ web/.env (ตั้งบนเซิร์ฟเวอร์เอง) tools/doc_scripts เอกสาร/รูป
ฝั่งเซิร์ฟเวอร์ : สำรอง DB เดิมไว้ที่ ~/db-backup ก่อนทับ → แตกไฟล์ → สร้าง venv ถ้ายังไม่มี
                → pip install -r requirements.txt → ติดตั้ง service classroom-web (ครั้งแรก)
                → restart เว็บ (service เปิดเองทุกครั้งที่บูต, รอเน็ตก่อน, ล่มแล้วเปิดใหม่เอง)
ระหว่างรันจะถามรหัส: scp 1 ครั้ง, ssh 1 ครั้ง, sudo 1 ครั้ง (ตั้ง SSH key แล้วเหลือแค่ sudo)
#>
param(
    [string]$Target = $(if ($env:NUC_TARGET) { $env:NUC_TARGET } else { 'bis@192.168.191.249' }),
    [string]$RemoteDir = 'Desktop/project-camere',
    [switch]$NoDb,
    [switch]$DryRun,
    [string]$EnvFile,  # ไฟล์ค่า SHEET_URL / SHEET_TOKEN บน PC -> ไปเป็น web/.env บนเซิร์ฟเวอร์ (ทับของเดิม)
    [string]$DbName    # ใช้ DB ชื่ออื่นแทน cameras.db (เช่น DB_NUG02.db) — ส่ง web/data/<ชื่อ> + secret.key และตั้ง CAMERA_DB_PATH ให้
)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$tmp = Join-Path $env:TEMP "deploy-$stamp"
New-Item -ItemType Directory -Path $tmp | Out-Null
$tgz = Join-Path $tmp 'deploy.tgz'
$sh = Join-Path $tmp 'deploy_remote.sh'

# 1) แพ็กไฟล์
$items = @('web', 'tools', 'tests', 'requirements.txt', 'requirements-dev.txt', 'pyproject.toml', 'README.md') |
    Where-Object { Test-Path (Join-Path $root $_) }
$exclude = @('--exclude=__pycache__', '--exclude=*.pyc', '--exclude=.pytest_cache', '--exclude=web/.env',
             '--exclude=tools/doc_scripts', '--exclude=*.jpg', '--exclude=*.mp4')
$dbExtra = @()
if ($DbName) {
    # DB แยกชื่อ: ไม่แพ็ก web/data ทั้งโฟลเดอร์ ส่งเฉพาะไฟล์ DB นี้ + secret.key
    $exclude += '--exclude=web/data'
    if (-not $NoDb) {
        $dbSrc = Join-Path $root "web/data/$DbName"
        if (-not (Test-Path $dbSrc)) { throw "ไม่พบ $dbSrc" }
        if (Test-Path "$dbSrc-wal") { Write-Warning "พบ $DbName-wal: ถ้ามีโปรแกรมเปิด DB นี้อยู่ ให้ปิดก่อน" }
        Copy-Item $dbSrc (Join-Path $tmp 'app.db')
        Copy-Item (Join-Path $root 'web/data/secret.key') (Join-Path $tmp 'app.key')
        $dbExtra = @((Join-Path $tmp 'app.db'), (Join-Path $tmp 'app.key'))
    }
}
elseif ($NoDb) { $exclude += '--exclude=web/data' }
else {
    # DB ที่กำลังเปิดอยู่จะมีข้อมูลค้างใน cameras.db-wal → ควรปิดเว็บบน PC ก่อน
    if (Test-Path (Join-Path $root 'web/data/cameras.db-wal')) {
        Write-Warning 'พบ cameras.db-wal: ถ้าเว็บบน PC ยังเปิดอยู่ ให้ปิดก่อน แล้วรันใหม่ (กันข้อมูลล่าสุดไม่ไปด้วย)'
    }
}
Write-Host "แพ็กไฟล์: $($items -join ', ')$(if ($NoDb) { ' (ไม่รวม DB)' } elseif ($DbName) { " + DB $DbName" } else { ' + web/data (DB)' })"
& tar.exe -czf $tgz -C $root @exclude @items
if ($LASTEXITCODE -ne 0) { throw 'tar ล้มเหลว' }
Write-Host ("ขนาด: {0:N1} MB" -f ((Get-Item $tgz).Length / 1MB))

# 2) สคริปต์ที่จะรันบนเซิร์ฟเวอร์ (เขียนแบบ LF ไม่ให้ bash งงกับ CRLF)
$withDb = if ($NoDb) { '0' } else { '1' }
$remote = @"
set -e
APP=~/$RemoteDir
mkdir -p "`$APP" ~/db-backup
if [ "$withDb" = "1" ]; then
  sudo systemctl stop classroom-web 2>/dev/null || true
fi
DBNAME="$DbName"
DBF="`$APP/web/data/`${DBNAME:-cameras.db}"
if ls "`$APP"/web/data/*.db >/dev/null 2>&1; then
  mkdir -p ~/db-backup/$stamp
  cp "`$APP"/web/data/* ~/db-backup/$stamp/ 2>/dev/null || true
  echo "สำรอง DB เดิม -> ~/db-backup/$stamp/"
fi
if [ "$withDb" = "1" ]; then
  rm -f "`$DBF-wal" "`$DBF-shm"
fi
tar -xzf ~/deploy.tgz -C "`$APP"
rm -f ~/deploy.tgz ~/deploy_remote.sh
cd "`$APP"
if [ -f ~/app.db ]; then
  mkdir -p web/data
  mv ~/app.db "web/data/`$DBNAME"
  mv ~/app.key web/data/secret.key
  chmod 600 "web/data/`$DBNAME" web/data/secret.key
  echo "วาง DB -> web/data/`$DBNAME"
fi
[ -f tools/nuc/RUN_MANUAL.txt ] && cp tools/nuc/RUN_MANUAL.txt ~/Desktop/RUN_MANUAL.txt && echo "อัปเดต ~/Desktop/RUN_MANUAL.txt (คำสั่งรัน Manual)"
# ไอคอน "Sync Sheet" บน Desktop: ดับเบิลคลิก = ดึงจาก Google Sheet ทันที
if [ -f tools/nuc/sync_sheet.sh ]; then
  chmod +x tools/nuc/sync_sheet.sh
  cat > ~/Desktop/sync-sheet.desktop <<DESK
[Desktop Entry]
Type=Application
Name=Sync Sheet
Comment=ดึงกล้อง/โปรเจกเตอร์จาก Google Sheet ทันที
Exec=gnome-terminal --title="Sync Sheet" -- bash `$APP/tools/nuc/sync_sheet.sh
Icon=view-refresh
Terminal=false
DESK
  chmod +x ~/Desktop/sync-sheet.desktop
  gio set ~/Desktop/sync-sheet.desktop metadata::trusted true 2>/dev/null || true
  echo "อัปเดต ~/Desktop/sync-sheet.desktop (ปุ่ม Sync Sheet)"
fi
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q -r requirements.txt
if [ -f ~/app.env ]; then mv ~/app.env web/.env; echo "อัปเดต web/.env (ค่าซิงก์ Google Sheet)"; fi
[ -f web/.env ] || touch web/.env
chmod 600 web/.env
if [ -n "`$DBNAME" ]; then
  # ให้เว็บใช้ DB ชื่อนี้ (ตั้งทุกครั้ง เพราะ web/.env อาจถูกทับด้วยไฟล์จาก PC)
  sed -i '/^CAMERA_DB_PATH=/d' web/.env
  [ -s web/.env ] && sed -i -e '`$a\' web/.env
  echo "CAMERA_DB_PATH=`$APP/web/data/`$DBNAME" >> web/.env
  echo "ใช้ DB: web/data/`$DBNAME"
fi
# service: เปิดเองตอนบูต รอเน็ตก่อน (สูงสุด 2 นาที แล้วเปิดต่อแม้ไม่มีเน็ต) ล่มแล้วเปิดใหม่เอง
cat > /tmp/classroom-web.service <<UNIT
[Unit]
Description=Classroom Monitor web
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=0

[Service]
User=`$(whoami)
WorkingDirectory=`$APP
EnvironmentFile=-`$APP/web/.env
ExecStartPre=-/usr/bin/timeout 120 /bin/sh -c 'until ping -c1 -W2 1.1.1.1 >/dev/null 2>&1; do sleep 2; done'
ExecStart=`$APP/.venv/bin/python web/app.py --host 0.0.0.0 --port 5000
Restart=always
RestartSec=5
TimeoutStartSec=180

[Install]
WantedBy=multi-user.target
UNIT
if ! cmp -s /tmp/classroom-web.service /etc/systemd/system/classroom-web.service; then
  echo "ติดตั้ง/อัปเดต service classroom-web (ถามรหัส sudo)"
  sudo cp /tmp/classroom-web.service /etc/systemd/system/classroom-web.service
  sudo systemctl daemon-reload
  sudo systemctl enable classroom-web
fi
rm -f /tmp/classroom-web.service
sudo systemctl restart classroom-web
sleep 3
systemctl is-active --quiet classroom-web && echo "เว็บทำงานแล้ว: http://`$(hostname -I | tr ' ' '\n' | grep -E '^(192\.168\.191|172\.28)\.' | head -1):5000" \
  || { echo "เว็บยังไม่ขึ้น ดู log:"; journalctl -u classroom-web -n 20 --no-pager; exit 1; }
echo "deploy เสร็จ ($stamp)"
"@
[IO.File]::WriteAllText($sh, ($remote -replace "`r`n", "`n"), (New-Object Text.UTF8Encoding $false))

if ($DryRun) { Write-Host "DryRun: ไฟล์อยู่ที่ $tmp"; return }

# 3) ส่ง + รัน (ถ้ายังไม่ได้ตั้ง SSH key จะถามรหัส 2 ครั้ง)
Write-Host "ส่งไป $Target ..."
$send = @($tgz, $sh) + $dbExtra
if ($EnvFile) {
    if (-not (Test-Path $EnvFile)) { throw "ไม่พบไฟล์ $EnvFile" }
    $envOut = Join-Path $tmp 'app.env'
    $envText = (Get-Content -Raw -Encoding UTF8 $EnvFile) -replace "`r`n", "`n"
    [IO.File]::WriteAllText($envOut, $envText, (New-Object Text.UTF8Encoding $false))
    $send += $envOut
    Write-Host "ส่ง $EnvFile ไปเป็น web/.env ด้วย"
}
& scp.exe @send "${Target}:~/"
if ($LASTEXITCODE -ne 0) { throw 'scp ล้มเหลว (เช็ก IP / ZeroTier / รหัสผ่าน)' }
& ssh.exe -t $Target 'bash ~/deploy_remote.sh'
if ($LASTEXITCODE -ne 0) { throw 'คำสั่งบนเซิร์ฟเวอร์ล้มเหลว (ดูข้อความด้านบน)' }
Remove-Item -Recurse -Force $tmp
