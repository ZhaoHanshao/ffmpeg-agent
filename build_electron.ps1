<#
.SYNOPSIS
    把 ffmpeg-agent 打包成 Windows 桌面应用（Electron 外壳 + PyInstaller 后端 + 前端 dist）。

.DESCRIPTION
    流程：
      0. 把 VERSION 同步进 electron/package.json（版本号只有一个来源）
      1. 构建前端（npm install + npm run build → frontend/dist）
      2. 构建后端（复用 build_exe.ps1 -KeepBuild：ONNX 模型 + Chroma 向量库 + PyInstaller onedir）
      3. 安装 Electron 依赖
      4. electron-builder 打包（默认出 NSIS 安装包 + 免安装 portable 两个 exe）
      5. 列出产物

    打出来的应用结构：
      FFmpeg Agent.exe          ← Electron 外壳
      resources/backend/        ← 整个 PyInstaller 产物（含 Python、依赖库、内置向量库）
      resources/frontend/dist/  ← 前端产物（改前端只需重跑第 1、4 步）

    数据（上传/产物/日志/向量库/ffmpeg）落在
    %APPDATA%\ffmpeg-agent\state\backend\ 下，所以装到 Program Files 也能正常写。

.PARAMETER Version
    指定版本号（如 0.2.0）。缺省读取 VERSION 文件。

.PARAMETER SkipFrontend
    跳过前端构建（已构建过 dist 时加速）。

.PARAMETER SkipBackend
    跳过 PyInstaller（已有 release\dist\ffmpeg-agent 时加速，改 Electron 代码时最常用）。

.PARAMETER SkipNpmInstall
    跳过 electron/ 的 npm install。

.PARAMETER DirOnly
    只产出免安装目录（release\electron\win-unpacked），不生成安装包。迭代时最快。

.PARAMETER Smoke
    打包前先跑一遍 Electron 冒烟测试（拉起后端 + 真实渲染页面 + 截图）。

.PARAMETER BundleFFmpeg
    把仓库 ffmpeg\ 下的 ffmpeg.exe / ffprobe.exe 一起打进安装包（体积 +约 300MB）。
    打上之后首次运行不再需要联网下载 ffmpeg；不打则首跑下载到用户数据目录。

.EXAMPLE
    .\build_electron.ps1
    .\build_electron.ps1 -SkipBackend -DirOnly      # 只改 Electron/前端时的快速回路
    .\build_electron.ps1 -Smoke
    .\build_electron.ps1 -BundleFFmpeg              # 完全离线安装包
#>

[CmdletBinding()]
param(
    [string]$Version,
    [switch]$SkipFrontend,
    [switch]$SkipBackend,
    [switch]$SkipNpmInstall,
    [switch]$DirOnly,
    [switch]$Smoke,
    [switch]$BundleFFmpeg
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

function Write-Step([string]$msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Assert-ExitOk([int]$code, [string]$msg) { if ($code -ne 0) { throw $msg } }

# 写无 BOM 的 UTF-8：PS 5.1 的 Set-Content -Encoding UTF8 会带 BOM，
# 而带 BOM 的 package.json 会让 Node 的 JSON.parse 直接抛错。
function Write-Utf8NoBom([string]$Path, [string]$Text) {
    [System.IO.File]::WriteAllText($Path, $Text, (New-Object System.Text.UTF8Encoding($false)))
}

# ── 0. 版本号 ──
if (-not $Version) {
    $Version = (Get-Content -Path (Join-Path $Root 'VERSION') -Raw).Trim()
}
if ($Version -notmatch '^\d+\.\d+\.\d+$') {
    throw "版本号格式无效: '$Version'，应为 x.y.z"
}
Write-Host "构建版本: $Version" -ForegroundColor Yellow

$py = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path $py)) { throw "未找到 .venv: $py，请先创建虚拟环境并安装 requirements.txt" }
if (-not (Get-Command npm -ErrorAction SilentlyContinue)) { throw '未找到 npm' }

# electron-builder 会在没有配置代理时直连 GitHub 拉 nsis/winCodeSign，
# 这里沿用 npm 的代理配置（如果配了），避免国内网络下卡住。
if (-not $env:HTTPS_PROXY -and -not $env:https_proxy) {
    $npmProxy = (& npm config get https-proxy 2>$null)
    if ($npmProxy -and $npmProxy -notmatch '^(null|undefined)$') {
        $env:HTTPS_PROXY = $npmProxy
        $env:HTTP_PROXY = $npmProxy
        Write-Host "  已按 npm 配置设置代理: $npmProxy" -ForegroundColor DarkGray
    }
}

$electronDir = Join-Path $Root 'electron'
$pkgPath = Join-Path $electronDir 'package.json'
$pkgRaw = Get-Content $pkgPath -Raw
$pkgNew = $pkgRaw -replace '("version"\s*:\s*")[^"]*(")', "`${1}$Version`${2}"
if ($pkgNew -ne $pkgRaw) {
    Write-Utf8NoBom $pkgPath $pkgNew
    Write-Host "  已同步 electron/package.json 版本号 → $Version" -ForegroundColor DarkGray
}

# ── 1. 前端 ──
if ($SkipFrontend) {
    Write-Step 'Step 1/5 跳过前端构建（-SkipFrontend）'
} else {
    Write-Step 'Step 1/5 构建前端'
    Push-Location (Join-Path $Root 'frontend')
    try {
        npm install --no-audit --no-fund
        Assert-ExitOk $LASTEXITCODE 'npm install (frontend) 失败'
        npm run build
        Assert-ExitOk $LASTEXITCODE 'npm run build 失败'
    } finally { Pop-Location }
}
if (-not (Test-Path (Join-Path $Root 'frontend\dist\index.html'))) {
    throw 'frontend/dist 不存在，请先构建前端或去掉 -SkipFrontend'
}

# ── 2. 后端（PyInstaller onedir）──
$backendExe = Join-Path $Root 'release\dist\ffmpeg-agent\ffmpeg-agent.exe'
if ($SkipBackend) {
    Write-Step 'Step 2/5 跳过后端构建（-SkipBackend）'
    if (-not (Test-Path $backendExe)) {
        throw "-SkipBackend 但找不到已有产物：$backendExe。请先完整跑一次 build_electron.ps1"
    }
    Write-Host "  复用: $backendExe" -ForegroundColor DarkGray
} else {
    Write-Step 'Step 2/5 构建 PyInstaller 后端（首次约 10~30 分钟）'
    # 复用 build_exe.ps1：它会准备 ONNX 嵌入模型、预构建 Chroma 向量库再打包；
    # -KeepBuild 是关键——默认它会把 release\dist 删掉，而 Electron 正要用那份产物。
    & (Join-Path $Root 'build_exe.ps1') -Version $Version -SkipFrontend -KeepBuild
    Assert-ExitOk $LASTEXITCODE 'build_exe.ps1 失败（后端打包失败）'
    if (-not (Test-Path $backendExe)) { throw "后端产物缺失: $backendExe" }
}

# ── 2.5 可选：把 ffmpeg 一起打进包里 ──
# 放进 PyInstaller 产物的 backend\bin\ 下（也就是 exe 旁），
# config.js 检测到两个 exe 都在就会把 FFMPEG_BIN_DIR 指过去，首跑不再联网下载。
if ($BundleFFmpeg) {
    Write-Step 'Step 2.5/5 打包 ffmpeg 二进制'
    $srcBin = Join-Path $Root 'ffmpeg'
    $dstBin = Join-Path $Root 'release\dist\ffmpeg-agent\backend\bin'
    foreach ($n in 'ffmpeg.exe', 'ffprobe.exe') {
        $src = Join-Path $srcBin $n
        if (-not (Test-Path $src)) {
            throw "-BundleFFmpeg 但找不到 $src。请先把 ffmpeg.exe / ffprobe.exe 放到仓库 ffmpeg\ 目录下。"
        }
    }
    New-Item -ItemType Directory -Force -Path $dstBin | Out-Null
    foreach ($n in 'ffmpeg.exe', 'ffprobe.exe') {
        Copy-Item (Join-Path $srcBin $n) $dstBin -Force
        $mb = [math]::Round((Get-Item (Join-Path $dstBin $n)).Length / 1MB, 1)
        Write-Host ("  已并入 {0} ({1} MB)" -f $n, $mb) -ForegroundColor DarkGray
    }
} else {
    # 明确清掉，避免上一次 -BundleFFmpeg 的残留悄悄留在包里（或反过来，
    # 让"这次没打 ffmpeg"变成一句可信的陈述）
    $dstBin = Join-Path $Root 'release\dist\ffmpeg-agent\backend\bin'
    if (Test-Path $dstBin) {
        Get-ChildItem $dstBin -Filter '*.exe' -ErrorAction SilentlyContinue | Remove-Item -Force
    }
}

# ── 3. Electron 依赖与图标 ──
Write-Step 'Step 3/5 准备 Electron'
if (-not (Test-Path (Join-Path $electronDir 'node_modules'))) { $SkipNpmInstall = $false }
Push-Location $electronDir
try {
    if (-not $SkipNpmInstall) {
        npm install --no-audit --no-fund
        Assert-ExitOk $LASTEXITCODE 'npm install (electron) 失败'
    } else {
        Write-Host '  跳过 npm install（-SkipNpmInstall）' -ForegroundColor DarkGray
    }

    # npm（>=11）默认不跑依赖的 install 脚本，electron 的二进制就下载不下来，
    # 表现为 node_modules\electron\dist\electron.exe 不存在。这里补跑一次它的安装脚本。
    if (-not (Test-Path (Join-Path $electronDir 'node_modules\electron\dist\electron.exe'))) {
        Write-Host '  未发现 electron 二进制，补跑 node_modules\electron\install.js ...' -ForegroundColor DarkGray
        $installJs = Join-Path $electronDir 'node_modules\electron\install.js'
        if (-not (Test-Path $installJs)) { throw '未找到 node_modules\electron\install.js，请先运行 npm install' }
        & node $installJs
        Assert-ExitOk $LASTEXITCODE 'electron 二进制下载失败（可设 ELECTRON_MIRROR 换镜像）'
        if (-not (Test-Path (Join-Path $electronDir 'node_modules\electron\dist\electron.exe'))) {
            throw 'electron 二进制仍不可用'
        }
    }

    if (-not (Test-Path (Join-Path $electronDir 'build\icon.ico'))) {
        Write-Warning '缺少 electron\build\icon.ico，安装包会用 Electron 默认图标。生成命令：python electron\build\make_icon.py（需要 Pillow）'
    }
} finally { Pop-Location }

# ── 3.5 冒烟测试（可选）──
if ($Smoke) {
    Write-Step 'Step 3.5/5 Electron 冒烟测试'
    Push-Location $electronDir
    try {
        & npx electron scripts/smoke.js
        Assert-ExitOk $LASTEXITCODE '冒烟测试失败'
    } finally { Pop-Location }
}

# ── 4. electron-builder ──
Write-Step 'Step 4/5 electron-builder 打包'
Push-Location $electronDir
try {
    $builderArgs = @('--win')
    if ($DirOnly) { $builderArgs += '--dir' }
    & npx electron-builder @builderArgs
    Assert-ExitOk $LASTEXITCODE 'electron-builder 失败'
} finally { Pop-Location }

# ── 5. 产物 ──
Write-Step 'Step 5/5 产物'
$outDir = Join-Path $Root 'release\electron'
$artifacts = @(Get-ChildItem $outDir -File -ErrorAction SilentlyContinue |
    Where-Object { $_.Extension -in '.exe', '.zip', '.blockmap' })
foreach ($f in $artifacts) {
    Write-Host ("  {0,-52} {1,8:N1} MB" -f $f.Name, ($f.Length / 1MB))
}
$unpacked = Join-Path $outDir 'win-unpacked'
if (Test-Path $unpacked) {
    $s = (Get-ChildItem $unpacked -Recurse -File | Measure-Object -Sum Length)
    Write-Host ("  {0,-52} {1,8:N1} MB" -f 'win-unpacked\', ($s.Sum / 1MB))
}
if (-not $artifacts -and -not (Test-Path $unpacked)) {
    throw "没有找到任何产物，请检查 electron-builder 输出：$outDir"
}
Write-Host "`n构建完成，产物在 $outDir" -ForegroundColor Green
