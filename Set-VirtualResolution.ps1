<#
.SYNOPSIS
  Run a Windows desktop larger than your display's native resolution (e.g. 3840x2160 on a 1920x1080 laptop
  panel). Windows renders the bigger desktop and scales it down, while the display keeps receiving its
  native signal.

.EXAMPLE
  .\Set-VirtualResolution.ps1 -List
  .\Set-VirtualResolution.ps1 -Width 3840 -Height 2160          # asks "Keep changes?", reverts after 15 s
  .\Set-VirtualResolution.ps1 -Width 2560 -Height 1440 -Display 2
  .\Set-VirtualResolution.ps1 -Native                           # back to the display's native resolution
#>
param(
    [int]$Width, [int]$Height,
    [switch]$Native,
    [switch]$List,
    # auto (built-in panel if present, else primary) | internal | primary | 2 | \\.\DISPLAY2
    [string]$Display = 'auto',
    [switch]$NoPrompt,        # apply and save without asking
    [switch]$SessionOnly,     # apply without saving (gone after sign-out/restart); used at startup
    [switch]$Quiet,           # no message boxes (for unattended use)
    [int]$ConfirmSeconds = 15,
    [int]$WaitForDisplaySeconds = 0,
    [int]$DelaySeconds = 0,
    [string]$WatchdogToken    # internal: run as the independent revert watchdog
)
$ErrorActionPreference = 'Stop'
Add-Type -Path (Join-Path $PSScriptRoot 'src\DisplayConfig.cs')
Add-Type -AssemblyName System.Windows.Forms, System.Drawing
[void][Vd]::SetProcessDpiAwarenessContext([IntPtr](-4))   # per-monitor DPI aware: work in physical pixels

$logDir = Join-Path $env:LOCALAPPDATA 'intel-virtual-display'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$logPath = Join-Path $logDir 'log.txt'
function Log($s) { Add-Content -LiteralPath $logPath -Value "$(Get-Date -Format o) $s"; Write-Host $s }
$token = $null
function Fail($s) {
    if ($token) { Set-Content -LiteralPath $token -Value 'failed' }
    Log "ERROR: $s"
    if (-not $Quiet -and -not $List) { [void][System.Windows.Forms.MessageBox]::Show($s, 'Virtual resolution', 'OK', 'Warning') }
    exit 1
}

# --- Watchdog mode: restore the saved setting if the confirmation never completed ------------------------
if ($WatchdogToken) {
    Start-Sleep -Seconds ($ConfirmSeconds + 10)
    if ((Test-Path -LiteralPath $WatchdogToken) -and (Get-Content -LiteralPath $WatchdogToken -Raw).Trim() -eq 'pending') {
        Log "Watchdog: no answer was recorded; restoring the saved display setting (result $([Vd]::ApplySaved()))."
    }
    Remove-Item -LiteralPath $WatchdogToken -ErrorAction SilentlyContinue
    exit 0
}

if ($DelaySeconds -gt 0) { Start-Sleep -Seconds $DelaySeconds }

function Get-State {
    $cfg = $null
    try { $cfg = [Vd]::Query($true) } catch { $cfg = [Vd]::Query($false) }   # older Windows: no virtual-mode query
    [pscustomobject]@{ Config = $cfg; Displays = @([Vd]::Describe($cfg)) }
}
function Select-Display($displays) {
    switch -Regex ($Display) {
        '^auto$'     { $d = $displays | Where-Object Internal | Select-Object -First 1; if (-not $d) { $d = $displays | Where-Object Primary | Select-Object -First 1 }; return $d }
        '^internal$' { return $displays | Where-Object Internal | Select-Object -First 1 }
        '^primary$'  { return $displays | Where-Object Primary | Select-Object -First 1 }
        '^\d+$'      { return $displays | Where-Object { $_.GdiName -eq "\\.\DISPLAY$Display" } | Select-Object -First 1 }
        default      { return $displays | Where-Object { $_.GdiName -eq $Display } | Select-Object -First 1 }
    }
}

$deadline = (Get-Date).AddSeconds($WaitForDisplaySeconds)
do {
    $state = Get-State; $target = Select-Display $state.Displays
    if ($target -or (Get-Date) -ge $deadline) { break }
    Start-Sleep -Seconds 2
} while ($true)

if ($List) {
    $state.Displays | ForEach-Object {
        [pscustomobject]@{
            Display = $_.GdiName -replace '^\\\\\.\\', ''; Name = $_.FriendlyName; Connection = $_.Output; GPU = $_.Vendor
            Native = "$($_.SignalWidth)x$($_.SignalHeight) @$([math]::Round($_.RefreshHz))Hz"; Desktop = "$($_.DesktopWidth)x$($_.DesktopHeight)"
            VirtualModes = $(if ($_.SupportsVirtualMode) { 'yes' } else { 'no (driver scaling fallback)' }); Primary = $_.Primary; BuiltIn = $_.Internal
        } } | Format-Table -AutoSize | Out-String -Width 220 | Write-Host
    exit 0
}

if (-not $target) { Fail "No display matches '$Display'. Run with -List to see the displays." }
if ($Native) { $Width = $target.SignalWidth; $Height = $target.SignalHeight }
if ($Width -le 0 -or $Height -le 0) { Fail 'Give -Width and -Height, or -Native.' }
$label = "${Width}x$Height on $($target.GdiName) ($($target.FriendlyName), $($target.Vendor) GPU, native $($target.SignalWidth)x$($target.SignalHeight))"
if ($target.DesktopWidth -eq $Width -and $target.DesktopHeight -eq $Height) { Log "Already at $label. Nothing to do."; exit 0 }

# In confirm mode, start the independent watchdog BEFORE touching the display, so the change is reverted even if
# this process dies at any point before an answer is recorded.
if (-not ($SessionOnly -or $NoPrompt)) {
    $token = Join-Path $env:TEMP "intel-virtual-display-$([guid]::NewGuid()).txt"
    Set-Content -LiteralPath $token -Value 'pending'
    Start-Process -FilePath 'powershell.exe' -WindowStyle Hidden -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
        "`"$PSCommandPath`"", '-WatchdogToken', "`"$token`"", '-ConfirmSeconds', $ConfirmSeconds) | Out-Null
}

# Prefer Windows virtual modes; fall back to asking the driver to scale a larger source mode.
$attempts = @()
if ($target.SupportsVirtualMode) { $attempts += @{ Name = 'virtual mode'; Config = [Vd]::WithVirtualDesktop($state.Config, $target.PathIndex, $Width, $Height) } }
$legacy = [Vd]::Query($false); $li = @([Vd]::Describe($legacy) | Where-Object { $_.GdiName -eq $target.GdiName })[0].PathIndex
$scaling = $(if ($Native) { [Vd]::SCALING_IDENTITY } else { [Vd]::SCALING_ASPECTRATIOCENTEREDMAX })
$attempts += @{ Name = 'driver scaling'; Config = [Vd]::WithLegacyDesktop($legacy, $li, $Width, $Height, $scaling) }

$applied = $null; $clamped = $false
foreach ($a in $attempts) {
    $r = [Vd]::Apply($a.Config, $false)
    if ($r -eq 0) {
        Start-Sleep -Milliseconds 700
        $now = @([Vd]::Describe([Vd]::Query($false)) | Where-Object { $_.GdiName -eq $target.GdiName })[0]
        if ($now.DesktopWidth -eq $Width -and $now.DesktopHeight -eq $Height) { $applied = $a; break }
        $clamped = $true
        Log "Using $($a.Name), Windows accepted the request but the desktop is $($now.DesktopWidth)x$($now.DesktopHeight)."
        [void][Vd]::ApplySaved()
    } else { Log "Using $($a.Name), the request was rejected (error $r)." }
}
if (-not $applied) {
    $hint = switch ($target.Vendor) {
        'AMD'    { ' On AMD you can also enable Virtual Super Resolution in AMD Software: Adrenalin Edition.' }
        'NVIDIA' { ' On NVIDIA you can also enable DSR / DLDSR in NVIDIA Control Panel or the NVIDIA app.' }
        default  { '' }
    }
    $why = ''
    if ($clamped -and $Width -gt $target.SignalWidth) {
        $why = ' Windows kept this display at its native size: larger-than-native desktops are not allowed on it (on Intel this typically works on built-in laptop panels only, not external monitors).'
    }
    Fail "Could not set $label. The display is unchanged.$why$hint"
}
Log "Applied $label using $($applied.Name) for this session."

if ($SessionOnly) { exit 0 }
if ($NoPrompt) { Log "Saved (result $([Vd]::Apply($applied.Config, $true)))."; exit 0 }

# --- Confirmation, like Windows' own "Keep these display settings?" -------------------------------------
$form = New-Object System.Windows.Forms.Form
$form.Text = 'Virtual resolution'; $form.TopMost = $true; $form.StartPosition = 'CenterScreen'
$form.FormBorderStyle = 'FixedDialog'; $form.MaximizeBox = $false; $form.MinimizeBox = $false
$form.AutoScaleMode = 'Dpi'; $form.Font = New-Object System.Drawing.Font('Segoe UI', 11)
$form.ClientSize = New-Object System.Drawing.Size(460, 150)
$text = New-Object System.Windows.Forms.Label; $text.SetBounds(16, 16, 430, 60); $form.Controls.Add($text)
$keepBtn = New-Object System.Windows.Forms.Button; $keepBtn.Text = 'Keep changes'; $keepBtn.SetBounds(170, 95, 130, 36)
$keepBtn.DialogResult = 'OK'; $form.Controls.Add($keepBtn); $form.AcceptButton = $keepBtn
$revertBtn = New-Object System.Windows.Forms.Button; $revertBtn.Text = 'Revert'; $revertBtn.SetBounds(310, 95, 130, 36)
$revertBtn.DialogResult = 'Cancel'; $form.Controls.Add($revertBtn); $form.CancelButton = $revertBtn
$script:left = $ConfirmSeconds
$update = { $text.Text = "Keep ${Width}x${Height}?`nReverting in $script:left seconds." }; & $update
$timer = New-Object System.Windows.Forms.Timer; $timer.Interval = 1000
$timer.Add_Tick({ $script:left--; & $update; if ($script:left -le 0) { $timer.Stop(); $form.DialogResult = 'Cancel'; $form.Close() } })
$form.Add_Shown({ $form.Activate(); $timer.Start() })
$keep = ($form.ShowDialog() -eq 'OK'); $timer.Stop(); $form.Dispose()

if ($keep) {
    Set-Content -LiteralPath $token -Value 'kept'
    Log "Kept and saved (result $([Vd]::Apply($applied.Config, $true)))."
} else {
    Set-Content -LiteralPath $token -Value 'reverted'
    Log "Reverted to the saved setting (result $([Vd]::ApplySaved()))."
}
