<#
.SYNOPSIS
  Builds dist\IntelVirtualDisplay.exe with the C# compiler that ships with Windows (.NET Framework 4.x).
  Nothing to install. The UI files in ui\ are embedded into the exe.
#>
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$csc = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if (-not (Test-Path $csc)) { $csc = Join-Path $env:WINDIR 'Microsoft.NET\Framework\v4.0.30319\csc.exe' }
if (-not (Test-Path $csc)) { throw '.NET Framework 4 C# compiler not found.' }

# App icon (.ico with 16/24/32/48/256 px PNG frames), drawn in the Paper palette. Generated once.
$ico = Join-Path $root 'app\app.ico'
if (-not (Test-Path $ico)) {
    Add-Type -AssemblyName System.Drawing
    $paper = [Drawing.ColorTranslator]::FromHtml('#f5f2eb'); $ink = [Drawing.ColorTranslator]::FromHtml('#1c1b18'); $red = [Drawing.ColorTranslator]::FromHtml('#d2462f')
    function RoundRect($x, $y, $w, $h, $r) {
        $p = New-Object Drawing.Drawing2D.GraphicsPath; $d = $r * 2
        $p.AddArc($x, $y, $d, $d, 180, 90); $p.AddArc($x + $w - $d, $y, $d, $d, 270, 90)
        $p.AddArc($x + $w - $d, $y + $h - $d, $d, $d, 0, 90); $p.AddArc($x, $y + $h - $d, $d, $d, 90, 90); $p.CloseFigure(); $p
    }
    $frames = foreach ($s in 16, 24, 32, 48, 256) {
        $bmp = New-Object Drawing.Bitmap $s, $s; $g = [Drawing.Graphics]::FromImage($bmp)
        $g.SmoothingMode = 'AntiAlias'; $k = $s / 64.0; $stroke = [math]::Max(1.0, 3 * $k)
        $g.FillPath((New-Object Drawing.SolidBrush $paper), (RoundRect (2*$k) (2*$k) (60*$k) (60*$k) (14*$k)))
        $g.DrawPath((New-Object Drawing.Pen $ink, $stroke), (RoundRect (2*$k) (2*$k) (60*$k) (60*$k) (14*$k)))
        $dash = New-Object Drawing.Pen $red, $stroke; if ($s -ge 32) { $dash.DashPattern = [single[]](2, 1.3) }
        $g.DrawPath($dash, (RoundRect (10*$k) (12*$k) (44*$k) (30*$k) (4*$k)))
        $g.FillPath((New-Object Drawing.SolidBrush $ink), (RoundRect (10*$k) (26*$k) (24*$k) (16*$k) (3*$k)))
        $cap = New-Object Drawing.Pen $ink, $stroke; $cap.StartCap = 'Round'; $cap.EndCap = 'Round'
        $g.DrawLine($cap, 22*$k, 52*$k, 42*$k, 52*$k); $g.Dispose()
        $ms = New-Object IO.MemoryStream; $bmp.Save($ms, [Drawing.Imaging.ImageFormat]::Png); $bmp.Dispose()
        [pscustomobject]@{ Size = $s; Data = $ms.ToArray() }
    }
    $out = New-Object IO.MemoryStream; $bw = New-Object IO.BinaryWriter $out
    $bw.Write([uint16]0); $bw.Write([uint16]1); $bw.Write([uint16]$frames.Count)
    $offset = 6 + 16 * $frames.Count
    foreach ($f in $frames) {
        $dim = if ($f.Size -ge 256) { 0 } else { $f.Size }
        $bw.Write([byte]$dim); $bw.Write([byte]$dim); $bw.Write([byte]0); $bw.Write([byte]0)
        $bw.Write([uint16]1); $bw.Write([uint16]32); $bw.Write([uint32]$f.Data.Length); $bw.Write([uint32]$offset)
        $offset += $f.Data.Length
    }
    foreach ($f in $frames) { $bw.Write($f.Data) }
    [IO.File]::WriteAllBytes($ico, $out.ToArray())
    Write-Host "Generated $ico"
}

$dist = Join-Path $root 'dist'; New-Item -ItemType Directory -Force $dist | Out-Null
$resources = Get-ChildItem (Join-Path $root 'ui') -Recurse -File | ForEach-Object {
    $name = 'ui/' + $_.FullName.Substring((Join-Path $root 'ui').Length + 1).Replace('\', '/')
    "/resource:`"$($_.FullName)`",$name"
}
$sources = @((Join-Path $root 'src\DisplayConfig.cs')) + @(Get-ChildItem (Join-Path $root 'app') -Filter *.cs | ForEach-Object FullName)
$argsList = @('/nologo', '/target:winexe', '/platform:anycpu', '/optimize+', '/langversion:5',
    "/out:$(Join-Path $dist 'IntelVirtualDisplay.exe')", "/win32icon:$ico", "/win32manifest:$(Join-Path $root 'app\app.manifest')",
    '/reference:System.dll', '/reference:System.Core.dll', '/reference:System.Drawing.dll', '/reference:System.Windows.Forms.dll',
    '/reference:System.Web.Extensions.dll', '/reference:Microsoft.CSharp.dll') + $resources + ($sources | ForEach-Object { "`"$_`"" })
& $csc @argsList
if ($LASTEXITCODE -ne 0) { throw "Build failed ($LASTEXITCODE)." }
Write-Host "Built $(Join-Path $dist 'IntelVirtualDisplay.exe')"
