<#
.SYNOPSIS
  Builds dist\IntelVirtualDisplay.exe (native Tkinter app, no web view) with PyInstaller.
  Needs Python 3.10+ with Tk; installs the build requirements on first run.
#>
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
Push-Location $root
try {
    python -m pip install --quiet --disable-pip-version-check -r requirements.txt pyinstaller
    python -m PyInstaller --noconfirm --clean --onefile --windowed --name IntelVirtualDisplay `
        --icon "$root\assets\app.ico" --add-data "$root\ivd\fonts;fonts" `
        --exclude-module numpy --exclude-module unittest --exclude-module pydoc --exclude-module doctest `
        --distpath "$root\dist" --workpath "$root\build" --specpath "$root\build" "$root\IntelVirtualDisplay.pyw"
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed ($LASTEXITCODE)." }
    Write-Host "Built $(Join-Path $root 'dist\IntelVirtualDisplay.exe')"
} finally { Pop-Location }
