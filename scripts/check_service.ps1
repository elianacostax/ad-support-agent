$procs = Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -like "*ad-support-agent*app.main*" }
if ($procs) {
    foreach ($p in $procs) {
        Write-Output ("RUNNING PID=" + $p.ProcessId + " START=" + $p.CreationDate + " CMD=" + $p.CommandLine)
    }
} else {
    Write-Output "NO_AD_SUPPORT_SERVICE_RUNNING"
}
