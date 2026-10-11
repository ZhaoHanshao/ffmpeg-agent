# 用无头浏览器给运行中的前端截图（Chrome/Edge）。
# 注意：无头 Chrome 需要命名管道（mojo IPC），在受限沙箱下会被拒绝，
# 调用本脚本的命令需要 sandbox_permissions=danger-full-access。
#
# 用法:
#   & .\frontend\_shot.ps1 -Out frontend\_shots\x.png [-Url ...] [-Width 1440] [-Height 900] [-Mode ffmpeg|ffprobe]
param(
  [Parameter(Mandatory = $true)][string]$Out,
  [string]$Url = 'http://127.0.0.1:8000/',
  [int]$Width = 1440,
  [int]$Height = 900,
  [int]$DelayMs = 3500,
  [ValidateSet('ffmpeg', 'ffprobe')][string]$Mode = 'ffmpeg'
)

$chrome = @(
  "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
  "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
  "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe",
  "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1

if (-not $chrome) { Write-Error 'no chrome/edge found'; exit 2 }

$outPath = [System.IO.Path]::GetFullPath($Out)
New-Item -ItemType Directory -Force -Path (Split-Path $outPath -Parent) | Out-Null
Remove-Item $outPath -ErrorAction SilentlyContinue

$profileDir = Join-Path $env:TEMP ('shot-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
New-Item -ItemType Directory -Force -Path $profileDir | Out-Null

# ffprobe 模式：页面加载后点击顶栏的 "FFprobe 分析" 按钮再截图
if ($Mode -eq 'ffprobe') {
  $tmpHtml = Join-Path $env:TEMP ('shot-' + [guid]::NewGuid().ToString('N').Substring(0, 8) + '.html')
  @"
<!doctype html><meta charset="utf-8">
<body style="margin:0">
<iframe src="$Url" style="border:0;width:${Width}px;height:${Height}px"></iframe>
<script>
setTimeout(function(){
  var f = document.querySelector('iframe');
  try {
    var btns = f.contentDocument.querySelectorAll('.mode-btn');
    if (btns.length > 1) btns[1].click();
  } catch (e) { document.title = 'CROSS_ORIGIN'; }
}, 1200);
</script>
</body>
"@ | Set-Content $tmpHtml -Encoding UTF8
  $Url = 'file:///' + ($tmpHtml -replace '\\', '/')
}

$args = @(
  '--headless=new',
  '--no-sandbox',
  '--disable-gpu',
  '--disable-crash-reporter',
  '--disable-breakpad',
  '--no-first-run',
  '--no-default-browser-check',
  '--disable-extensions',
  '--hide-scrollbars',
  '--force-device-scale-factor=1',
  "--user-data-dir=$profileDir",
  "--window-size=$Width,$Height",
  "--screenshot=$outPath",
  "--virtual-time-budget=$DelayMs",
  $Url
)

& $chrome @args 2>&1 | Select-String -Pattern 'FATAL|ERROR' | Select-Object -First 3
Start-Sleep -Milliseconds 400
Remove-Item $profileDir -Recurse -Force -ErrorAction SilentlyContinue

if (Test-Path $outPath) {
  Write-Host ("OK: {0} ({1:N1} KB)" -f $outPath, ((Get-Item $outPath).Length / 1KB))
  exit 0
}
Write-Host 'FAILED: screenshot not produced'
exit 1
