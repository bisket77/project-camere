<#
เปิดเว็บกล้องบน NUC แบบมีทางสำรอง: ลองทีละที่อยู่ ตัวไหนติดก่อนเปิดตัวนั้น
  1) ชื่อในวงแลน (mDNS)      bis.local          -> ใช้ได้เมื่ออยู่วงเดียวกับ NUC
  2) ชื่อที่ตั้งเองใน hosts   bis.lab           -> ชี้ไปที่ IP ZeroTier (C:\Windows\System32\drivers\etc\hosts)
  3) IP ZeroTier             192.168.191.249    -> ใช้ได้ทุกที่ที่ PC เปิด ZeroTier

ตัวอย่าง:
  .\tools\open_nuc.ps1            # เปิดเว็บในเบราว์เซอร์
  .\tools\open_nuc.ps1 -Ssh       # ต่อ SSH แทน
  .\tools\open_nuc.ps1 -Check     # แค่บอกว่าที่อยู่ไหนติด
#>
param(
    [string[]]$Hosts = $(if ($env:NUC_HOSTS) { $env:NUC_HOSTS -split ',' } else { @('bis.local', 'bis.lab', '192.168.191.249') }),
    [int]$Port = 5000,
    [string]$User = 'bis',
    [switch]$Ssh,
    [switch]$Check
)

function Test-Port([string]$h, [int]$p, [int]$ms = 1500) {
    $c = New-Object Net.Sockets.TcpClient
    try {
        $ar = $c.BeginConnect($h, $p, $null, $null)
        if ($ar.AsyncWaitHandle.WaitOne($ms) -and $c.Connected) { $c.EndConnect($ar); return $true }
        return $false
    } catch { return $false } finally { $c.Close() }
}

$port = if ($Ssh) { 22 } else { $Port }
$found = $null
foreach ($h in $Hosts) {
    $ok = Test-Port $h $port
    Write-Host ("{0,-18} พอร์ต {1,-5} {2}" -f $h, $port, $(if ($ok) { 'ติด' } else { 'ไม่ติด' }))
    if ($ok -and -not $found) { $found = $h; if (-not $Check) { break } }
}

if (-not $found) {
    Write-Warning 'ต่อไม่ได้ทุกที่อยู่: เช็กว่า NUC เปิดอยู่, ZeroTier บน PC เปิดอยู่, และเว็บ/SSH บน NUC ทำงาน'
    if (-not $Ssh) { Write-Host 'บน NUC: sudo systemctl status classroom-web --no-pager' }
    exit 1
}
if ($Check) { return }
if ($Ssh) { & ssh.exe "$User@$found" }
else {
    $url = "http://${found}:$Port"
    Write-Host "เปิด $url"
    Start-Process $url
}
