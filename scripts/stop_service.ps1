$procs = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -like "*ad-support-agent*app.main*" }
if ($procs) {
    foreach ($p in $procs) {
        Write-Output ("KILL PID=" + $p.ProcessId)
        Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Seconds 2
} else {
    Write-Output "NO_PROCESS_TO_KILL"
}
$left = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -like "*ad-support-agent*app.main*" }
if ($left) {
    Write-Output "STILL_RUNNING"
} else {
    Write-Output "ALL_STOPPED"
}
