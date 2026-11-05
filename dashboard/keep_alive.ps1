# Trading Floor (http://127.0.0.1:3471): starts the dashboard and restarts it if it stops.
# Launched at logon by the scheduled task "Salle des marches".
$py = "C:\Users\everd\AppData\Roaming\uv\python\cpython-3.11.15-windows-x86_64-none\python.exe"
$root = "C:\Users\everd\Trading-bot-dev"
$log = Join-Path $root "dashboard\keep_alive.log"
while ($true) {
    $up = Get-NetTCPConnection -LocalPort 3471 -State Listen -ErrorAction SilentlyContinue
    if (-not $up) {
        "$(Get-Date -Format s) dashboard down, starting" | Add-Content $log
        Start-Process -FilePath $py -ArgumentList "dashboard/server.py" -WorkingDirectory $root -WindowStyle Hidden
    }
    Start-Sleep -Seconds 60
}
