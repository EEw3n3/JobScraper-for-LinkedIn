# Build JobScraper.exe (the desktop window) into this folder.
# The .exe only shows the window; the work runs in .venv, so rebuild only after changing app\.
Set-Location $PSScriptRoot
$root = $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
& $python -m PyInstaller --noconfirm --clean --onefile --windowed --name JobScraper `
    --icon "$root\app\icon.ico" --add-data "$root\app\ui;ui" --distpath "$root" `
    --workpath "$root\build\pyinstaller" --specpath "$root\build" "$root\app\jobscraper_app.py"
