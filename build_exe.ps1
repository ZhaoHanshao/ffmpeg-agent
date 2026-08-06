<#
.SYNOPSIS
    构建 ffmpeg-agent 的 Windows 便携版 exe（PyInstaller onedir）并打包成 zip。
    依赖：仓库内 .venv（已装 pyinstaller 等），前端 node/npm，联网（下载 ffmpeg essentials）。

.DESCRIPTION
    流程：
      1. 确定版本号（默认读取 VERSION）
      2. 构建前端（npm install + npm run build → frontend/dist）
      3. 下载 gyan.dev ffmpeg essentials（缓存到 release/tools），解出 ffmpeg.exe/ffprobe.exe 到 ffmpeg/
      4. 运行 PyInstaller 生成 release/dist/ffmpeg-agent（onedir, 无控制台）
      5. 压缩为 release/ffmpeg-agent-win64-v<版本>.zip

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

# ── 2. ffmpeg 二进制 ──
Write-Step 'Step 2/4 准备 ffmpeg/ffprobe 二进制'
$ffmpegDir = Join-Path $Root 'ffmpeg'
if (-not (Test-Path (Join-Path $ffmpegDir 'ffmpeg.exe'))) {
    $toolsDir = Join-Path $Root 'release\tools'
    New-Item -ItemType Directory -Force -Path $toolsDir | Out-Null
    $binDir = $null

    # 尝试多个来源：gyan.dev → BtbN(GitHub) → 本机已安装的 ffmpeg
    $sources = @(
        'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip',
        'https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip'
    )
    foreach ($url in $sources) {
        $zipPath = Join-Path $toolsDir ('ffmpeg-' + ([IO.Path]::GetFileNameWithoutExtension($url).Replace('ffmpeg-', '')) + '.zip')
        Write-Host "  下载 ffmpeg: $url ..."
        try {
            Invoke-WebRequest -Uri $url -OutFile $zipPath -TimeoutSec 600
            if (Test-Path $zipPath -and (Get-Item $zipPath).Length -gt 10MB) {
                $unzipDir = Join-Path $toolsDir ("ffmpeg-" + ([IO.Path]::GetFileNameWithoutExtension($url).Replace('ffmpeg-', '')))
                if (Test-Path $unzipDir) { Remove-Item -Recurse -Force $unzipDir }
                Expand-Archive -Path $zipPath -DestinationPath $unzipDir -Force
                $binDir = Get-ChildItem $unzipDir -Recurse -Directory -Filter 'bin' | Select-Object -First 1
                if ($binDir) { break }
            }
        } catch {
            Write-Warning "  下载失败: $($_.Exception.Message)"
        }
    }

    # 兜底：从本机已安装的 ffmpeg 复制
    if (-not $binDir) {
        Write-Host '  在线下载失败，尝试从本机 ffmpeg 复制 ...'
        $localBins = @(
            (Join-Path $env:USERPROFILE 'scoop\apps\ffmpeg\current\bin'),
            'D:\软件\工具\ffmpeg'
        ) | ForEach-Object { Get-ChildItem $_ -Recurse -Filter 'ffmpeg.exe' -ErrorAction SilentlyContinue | ForEach-Object { $_.DirectoryName } }
        $localBin = $localBins | Select-Object -First 1
        if (-not $localBin) {
            $localBin = Get-ChildItem 'D:\' -Recurse -Filter 'ffmpeg.exe' -ErrorAction SilentlyContinue -Depth 4 |
                Where-Object { $_.FullName -match '\\bin\\ffmpeg\.exe$' } |
                Select-Object -First 1 -ExpandProperty DirectoryName
        }
        if ($localBin) {
            $binDir = [pscustomobject]@{ FullName = $localBin }
        }
    }

    if (-not $binDir -or -not (Test-Path (Join-Path $binDir.FullName 'ffmpeg.exe'))) {
        throw '未能获取 ffmpeg/ffprobe：gyan.dev 与 BtbN 均下载失败，且未在本机找到 ffmpeg.exe。请手动放置到 ffmpeg/ 目录后重试'
    }
    New-Item -ItemType Directory -Force -Path $ffmpegDir | Out-Null
    Copy-Item (Join-Path $binDir.FullName 'ffmpeg.exe') $ffmpegDir -Force
    Copy-Item (Join-Path $binDir.FullName 'ffprobe.exe') $ffmpegDir -Force
    Write-Host "  ffmpeg/ffprobe 复制到 $ffmpegDir"
} else {
    Write-Host '  ffmpeg/ffprobe 已存在，跳过下载'
}

# ── 3. PyInstaller 打包 ──
Write-Step 'Step 3/4 PyInstaller 打包（耗时较长）'
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

# ── 4. 压缩发布包 ──
Write-Step 'Step 4/4 压缩发布包'
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