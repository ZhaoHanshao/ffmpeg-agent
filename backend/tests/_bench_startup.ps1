# Repeated cold-start measurement for both dependency profiles; prints median.
# ASCII-only (PS 5.1 reads .ps1 as ANSI without BOM).
param([int]$Runs = 3)

$root = (Get-Location).Path
$python = Join-Path $root '.venv\Scripts\python.exe'
$launcher = Join-Path $root 'backend\tests\_launch_blocked.py'

function Measure-Once([string]$mode) {
  $argList = @($launcher)
  if ($mode -eq 'baseline') { $argList += '--baseline' }
  $out = Join-Path $env:TEMP "loop-$mode.out.log"
  $err = Join-Path $env:TEMP "loop-$mode.err.log"
  Remove-Item $out, $err -ErrorAction SilentlyContinue

  $sw = [System.Diagnostics.Stopwatch]::StartNew()
  $p = Start-Process -FilePath $python -ArgumentList $argList -WorkingDirectory $root `
       -RedirectStandardOutput $out -RedirectStandardError $err -PassThru -WindowStyle Hidden
  $t = $null
  while ($sw.Elapsed.TotalSeconds -lt 240) {
    if ($p.HasExited) { break }
    try {
      $j = (Invoke-WebRequest 'http://127.0.0.1:8000/api/health' -TimeoutSec 2 -UseBasicParsing).Content | ConvertFrom-Json
      if ($j.status -eq 'ok') { $t = $sw.Elapsed.TotalSeconds; break }
    } catch {}
    Start-Sleep -Milliseconds 100
  }
  if (-not $p.HasExited) { try { Stop-Process -Id $p.Id -Force } catch {} }
  $p.WaitForExit(15000) | Out-Null
  Start-Sleep -Seconds 2
  return $t
}

foreach ($mode in 'baseline', 'clean') {
  $times = @()
  for ($i = 1; $i -le $Runs; $i++) {
    $t = Measure-Once $mode
    if ($null -eq $t) { Write-Host ("  [{0}] run {1}: FAILED" -f $mode, $i); continue }
    $times += [double]$t
    Write-Host ("  [{0}] run {1}: {2:N2}s" -f $mode, $i, $t)
  }
  if ($times.Count -gt 0) {
    $sorted = $times | Sort-Object
    $median = $sorted[[int][math]::Floor($sorted.Count / 2)]
    Write-Host ("  [{0}] median = {1:N2}s  (min {2:N2}s / max {3:N2}s)`n" -f `
      $mode, $median, $sorted[0], $sorted[-1])
  }
}
