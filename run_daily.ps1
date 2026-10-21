# Daily run: collect new LinkedIn jobs, sort Gmail replies, add the job list to Gmail and,
# once enabled in config\applicant.json, apply to the best matches with Easy Apply.
# -Hibernate: afterwards hibernate the laptop, but only if nobody has used it for 10 minutes.
# Hibernate (not shutdown) lets the scheduled task wake it again the next morning.
# Log: results\daily.log
param([switch]$Hibernate)

Set-Location $PSScriptRoot
$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
New-Item -ItemType Directory -Force results | Out-Null
Start-Transcript -Append -Path "results\daily.log" | Out-Null
# Piping through PowerShell is what makes Python's output reach the transcript.
$env:PYTHONUNBUFFERED = "1"
$env:PYTHONIOENCODING = "utf-8"
try {
    # AI scoring against cv_storage runs only once a Gemini key is in .env.
    $ai = @("--no-ai")
    if ((Test-Path ".env") -and (Select-String -Quiet -Pattern "^GEMINI_API_KEY=\S" -Path ".env")) { $ai = @() }
    & $python main.py scrape --headless --browsers 1 @ai 2>&1 | ForEach-Object { "$_" }
    $db = Get-ChildItem "results\jobs_*.db" | Sort-Object LastWriteTime | Select-Object -Last 1
    if ($null -ne $db) {
        & $python main.py mail --digest-db $db.FullName 2>&1 | ForEach-Object { "$_" }
    } else {
        & $python main.py mail 2>&1 | ForEach-Object { "$_" }
    }
    # Applications are sent only after "auto_submit": true is set in config\applicant.json.
    $applicant = "config\applicant.json"
    if ($null -ne $db -and (Test-Path $applicant) -and
            (Get-Content -Raw -Encoding UTF8 $applicant | ConvertFrom-Json).auto_submit -eq $true) {
        & $python main.py apply --db $db.FullName 2>&1 | ForEach-Object { "$_" }
    }
} finally {
    Stop-Transcript | Out-Null
}

if ($Hibernate) {
    Add-Type @"
using System;
using System.Runtime.InteropServices;
public static class IdleTime {
    [StructLayout(LayoutKind.Sequential)]
    struct LASTINPUTINFO { public uint cbSize; public uint dwTime; }
    [DllImport("user32.dll")] static extern bool GetLastInputInfo(ref LASTINPUTINFO info);
    public static uint Seconds() {
        var info = new LASTINPUTINFO();
        info.cbSize = (uint)Marshal.SizeOf(info);
        GetLastInputInfo(ref info);
        return ((uint)Environment.TickCount - info.dwTime) / 1000;
    }
}
"@
    $idle = [IdleTime]::Seconds()
    if ($idle -ge 600) {
        Add-Content "results\daily.log" "Idle for $idle s, hibernating."
        shutdown.exe /h
    } else {
        Add-Content "results\daily.log" "Laptop is in use (idle $idle s), not hibernating."
    }
}
