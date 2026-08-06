<#
.SYNOPSIS
    构建 ffmpeg-agent 的 Windows 便携版 exe（PyInstaller onedir）并打包成 zip。
    依赖：仓库内 .venv（已装 pyinstaller 等），前端 node/npm，联网（下载 ffmpeg essentials）。

.DESCRIPTION
    流程：
      1. 确定版本号（默认读取 VERSION）
      2. 构建前端（npm install + npm run build → frontend/dist）
      3. 导出 ONNX 嵌入模型（backend/build_bge_onnx.py → backend/data/bge_onnx）
      4. 运行 PyInstaller 生成 release/dist/ffmpeg-agent（onedir, 无控制台）
      5. 压缩为 release/ffmpeg-agent-win64-v<版本>.zip
      （ffmpeg/ffprobe 不再打包，由应用首跑自动下载到 exe 旁 backend\bin\）

.PARAMETER Version
    指定版本号（如 0.2.0）。缺省读取 VERSION 文件。

.PARAMETER SkipFrontend
    跳过前端构建（已构建过 dist 时加速）。

.PARAMETER KeepBuild
    保留 PyInstaller 的 build/ 中间目录（默认删除）。

.EXAMPLE
    .\build_exe.ps1
    .\build_exe.ps1 -Version 0.2.0
#>

[CmdletBinding()]
param(
    [string]$Version,
    [switch]$SkipFrontend,
    [switch]$KeepBuild
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

function Write-Step([string]$msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Assert-ExitOk([int]$code, [string]$msg) { if ($code -ne 0) { throw $msg } }

if (-not $Version) {
    $Version = (Get-Content -Path (Join-Path $Root 'VERSION') -Raw).Trim()
}
Write-Host "构建版本: $Version" -ForegroundColor Yellow

$Py = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path $Py)) { throw "未找到 .venv: $Py，请先创建虚拟环境并安装 requirements.txt" }
if (-not (Get-Command npm -ErrorAction SilentlyContinue)) { throw '未找到 npm' }

# ── 1. 前端构建 ──
if (-not $SkipFrontend) {
    Write-Step 'Step 1/4 构建前端'
    Push-Location (Join-Path $Root 'frontend')
    try {
        npm install
        Assert-ExitOk $LASTEXITCODE 'npm install 失败'
        npm run build
        Assert-ExitOk $LASTEXITCODE 'npm run build 失败'
    } finally { Pop-Location }
}
if (-not (Test-Path (Join-Path $Root 'frontend\dist\index.html'))) {
    throw 'frontend/dist 不存在，请先构建前端或使用 -SkipFrontend'
}

# ── 2. 导出 ONNX 嵌入模型 ──
Write-Step 'Step 2/4 导出 ONNX 嵌入模型（backend/data/bge_onnx）'
$onnxDir = Join-Path $Root 'backend\data\bge_onnx'
if (Test-Path (Join-Path $onnxDir 'model.onnx')) {
    Write-Host '  model.onnx 已存在，跳过导出'
} else {
    & $Py (Join-Path $Root 'backend\build_bge_onnx.py')
    Assert-ExitOk $LASTEXITCODE 'ONNX 模型导出失败'
}
if (-not (Test-Path (Join-Path $onnxDir 'model.onnx'))) {
    throw '未找到 backend/data/bge_onnx/model.onnx'
}

# ── 3. 预构建向量库（离线，用本地文档缓存 + ONNX 嵌入器） ──
Write-Step 'Step 3/5 预构建 Chroma 向量库（backend/data/chroma_db）'
$dbFile = Join-Path $Root 'backend\data\chroma_db\chroma.sqlite3'
if (Test-Path $dbFile) {
    Write-Host '  chroma_db 已存在，跳过构建'
} else {
    & $Py (Join-Path $Root 'backend\build_package_db.py')
    Assert-ExitOk $LASTEXITCODE '向量库构建失败（需 backend/data/docs 下的 ffmpeg-all.html / ffprobe-all.html，或用 .\build_package_db.py 联网抓取）'
}
if (-not (Test-Path $dbFile)) {
    throw '未找到 backend/data/chroma_db/chroma.sqlite3'
}

# ── 4. PyInstaller 打包 ──
Write-Step 'Step 4/5 PyInstaller 打包（耗时较长）'
$distDir = Join-Path $Root 'release\dist'
$workDir = Join-Path $Root 'release\build'
& $Py -m PyInstaller --noconfirm --clean `
    --distpath $distDir `
    --workpath $workDir `
    (Join-Path $Root 'ffmpeg-agent.spec')
Assert-ExitOk $LASTEXITCODE 'PyInstaller 构建失败'

$appDir = Join-Path $distDir 'ffmpeg-agent'
if (-not (Test-Path (Join-Path $appDir 'ffmpeg-agent.exe'))) {
    throw "构建产物缺失: $appDir\ffmpeg-agent.exe"
}

# ── 5. 压缩发布包 ──
Write-Step 'Step 5/5 压缩发布包'
$outZip = Join-Path $Root "release\ffmpeg-agent-win64-v$Version.zip"
if (Test-Path $outZip) { Remove-Item -Force $outZip }
# 优先 tar (支持 zip64/大体积)，否则 Compress-Archive
if (Get-Command tar.exe -ErrorAction SilentlyContinue) {
    Push-Location $distDir
    try {
        tar -a -c -f $outZip ffmpeg-agent
        Assert-ExitOk $LASTEXITCODE 'tar 压缩失败'
    } finally { Pop-Location }
} else {
    Compress-Archive -Path $appDir -DestinationPath $outZip -CompressionLevel Optimal -Force
}

if (-not $KeepBuild) {
    Remove-Item -Recurse -Force $workDir -ErrorAction SilentlyContinue
    Remove-Item -Recurse -Force $distDir -ErrorAction SilentlyContinue
}

$sizeMB = [math]::Round((Get-Item $outZip).Length / 1MB, 1)
Write-Host "`n构建完成: $outZip ($sizeMB MB)" -ForegroundColor Green
Write-Output "OUTZIP=$outZip"