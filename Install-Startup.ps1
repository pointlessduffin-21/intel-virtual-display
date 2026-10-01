<#
.SYNOPSIS
  Apply a virtual resolution automatically every time you sign in, via a shortcut in your Startup folder.

.EXAMPLE
  .\Install-Startup.ps1 -Width 3840 -Height 2160
  .\Install-Startup.ps1 -Width 2560 -Height 1440 -Display 2
  .\Install-Startup.ps1 -Remove
#>
param([int]$Width = 3840, [int]$Height = 2160, [string]$Display = 'auto', [switch]$Remove)
$ErrorActionPreference = 'Stop'
$shortcut = Join-Path ([Environment]::GetFolderPath('Startup')) 'Virtual Resolution.lnk'

if ($Remove) {
    if (Test-Path -LiteralPath $shortcut) { Remove-Item -LiteralPath $shortcut; Write-Host "Removed $shortcut" } else { Write-Host 'No startup entry installed.' }
    exit 0
}

$script = Join-Path $PSScriptRoot 'Set-VirtualResolution.ps1'
# At sign-in: wait for the display to appear, apply for this session only (nothing is saved, so if a
# resolution ever misbehaves, removing this shortcut and restarting fully undoes it), and show no dialogs.
$arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$script`" -Width $Width -Height $Height " +
             "-Display $Display -SessionOnly -Quiet -DelaySeconds 3 -WaitForDisplaySeconds 60"
$shell = New-Object -ComObject WScript.Shell
$lnk = $shell.CreateShortcut($shortcut)
$lnk.TargetPath = "$env:WINDIR\System32\WindowsPowerShell\v1.0\powershell.exe"
$lnk.Arguments = $arguments
$lnk.WorkingDirectory = $PSScriptRoot
$lnk.WindowStyle = 7   # minimized
$lnk.Description = "Set a ${Width}x$Height virtual desktop at sign-in (intel-virtual-display)"
$lnk.Save()
Write-Host "Installed: $shortcut"
Write-Host "At every sign-in the desktop is set to ${Width}x$Height (display: $Display). Keep this folder where it is."
