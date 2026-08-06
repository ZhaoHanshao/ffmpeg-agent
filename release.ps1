<#
.SYNOPSIS
    一键生成 ffmpeg-agent 的 GitHub Release：本地构建前端、打包 zip、打 tag、gh 创建 Release，并按需构建 Windows exe 包、推送 Docker 镜像到 GHCR。

.DESCRIPTION
    流程：
      1. 检查 gh 登录状态与 write:packages 权限
      2. 确定版本号（默认读取 VERSION 文件，可用 -Version 覆盖）
      3. 构建前端（npm install + npm run build）
      4. 组装并压缩 ffmpeg-agent-v<版本>.zip 到 release/ 目录
      5. 打 tag v<版本> 并推送 origin
      6. gh release create v<版本> <zip> --generate-notes
      7. （-Exe 时）调用 build_exe.ps1 构建 Windows exe 包并上传为 release 资产
      8. （除非 -SkipDocker）docker build + push ghcr.io/ZhaoHanshao/ffmpeg-agent 并设为 public

.PARAMETER Version
    指定版本号（如 0.2.0）。缺省读取 VERSION 文件的当前值（如 0.1.0）。

.PARAMETER Exe
    同时构建 Windows 便携版 exe 包（调 build_exe.ps1，耗时 15~30 分钟）并上传到 Release。

.PARAMETER SkipDocker
    跳过 Docker 镜像构建与推送。

.PARAMETER Yes
    跳过所有交互确认（工作区有未提交修改时也继续）。

.EXAMPLE
    .\release.ps1                     # 按 VERSION 文件发版
    .\release.ps1 -Version 0.2.0      # 指定版本
    .\release.ps1 -SkipDocker         # 只出 zip + Release，不推镜像
    .\release.ps1 -Exe -SkipDocker    # 源码 zip + Windows exe 包，不推镜像
#>

[CmdletBinding()]
param(
    [string]$Version,
    [switch]$Exe,
    [switch]$SkipDocker,
    [switch]$Yes
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

$RepoOwner = 'ZhaoHanshao'
$RepoName = 'ffmpeg-agent'
$StageBase = Join-Path $Root 'release'

function Write-Step([string]$msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Assert-ExitOk([int]$code, [string]$msg) {
    if ($code -ne 0) { throw $msg }
}

# PS 5.1: EAP=Stop 下重定向原生命令 stderr 会抛 NativeCommandError，此函数临时放宽
function Invoke-Quiet([scriptblock]$cmd) {
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { & $cmd } finally { $ErrorActionPreference = $prev }
}

function Confirm-Continue([string]$msg) {
    if ($Yes) { return }
    $ans = Read-Host "$msg [y/N]"
    if ($ans -notmatch '^[yY]') { Write-Host '已取消。'; exit 0 }
}

# ── 1. 前置检查 ──
Write-Step 'Step 1/7 检查环境（gh / node / npm）'
foreach ($cmd in 'gh', 'node', 'npm') {
    if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) {
        throw "未找到命令: $cmd"
    }
}
Invoke-Quiet { gh auth status *> $null }
Assert-ExitOk $LASTEXITCODE 'gh 未登录，请先运行 gh auth login'
Write-Host '  gh 已登录' -ForegroundColor Green

# ── 2/3. 版本号与工作区 ──
Write-Step 'Step 2/7 确定版本号'
if (-not $Version) {
    $Version = (Get-Content -Path (Join-Path $Root 'VERSION') -Raw).Trim()
}
if ($Version -notmatch '^\d+\.\d+\.\d+$') {
    throw "版本号格式无效: '$Version'，应为 x.y.z"
}
$Tag = "v$Version"
$ZipName = "ffmpeg-agent-$Tag.zip"
$ZipPath = Join-Path $StageBase $ZipName
Write-Host "  版本: $Version   tag: $Tag   zip: $ZipName" -ForegroundColor Yellow

Write-Step 'Step 3/7 检查 git 工作区'
$dirty = git status --porcelain
if ($dirty) {
    Write-Warning '存在未提交的修改，打包将包含当前工作区内容：'
    Write-Host ($dirty -join "`n")
    Confirm-Continue '包含未提交修改继续？'
} else {
    Write-Host '  工作区干净' -ForegroundColor Green
}

# ── 4. 构建前端 ──
Write-Step 'Step 4/7 构建前端 (npm ci + npm run build)'
Push-Location (Join-Path $Root 'frontend')
try {
    npm install
    Assert-ExitOk $LASTEXITCODE 'npm install 失败'
    npm run build
    Assert-ExitOk $LASTEXITCODE 'npm run build 失败'
} finally {
    Pop-Location
}
Write-Host '  前端构建完成' -ForegroundColor Green

# ── 5. 打包 zip ──
Write-Step "Step 5/7 打包 $ZipName"
$stage = Join-Path $StageBase "staging\ffmpeg-agent-$Tag"
if (Test-Path $stage) { Remove-Item -Recurse -Force $stage }
foreach ($d in @($StageBase, (Join-Path $stage 'backend'), (Join-Path $stage 'frontend'))) {
    New-Item -ItemType Directory -Force -Path $d | Out-Null
}

Copy-Item (Join-Path $Root 'backend\app') (Join-Path $stage 'backend\app') -Recurse -Force
Copy-Item (Join-Path $Root 'frontend\dist') (Join-Path $stage 'frontend\dist') -Recurse -Force
Copy-Item (Join-Path $Root 'frontend\src') (Join-Path $stage 'frontend\src') -Recurse -Force
foreach ($f in 'index.html', 'vite.config.js', 'package.json', 'package-lock.json') {
    Copy-Item (Join-Path $Root "frontend\$f") (Join-Path $stage 'frontend') -Force
}
foreach ($f in 'requirements.txt', 'Dockerfile', 'README.MD', 'VERSION') {
    Copy-Item (Join-Path $Root $f) $stage -Force
}

foreach ($p in @('backend\data', 'backend\upload', 'backend\download', 'backend\tests')) {
    $src = Join-Path $stage $p
    if (Test-Path $src) { Remove-Item -Recurse -Force $src }
}

Get-ChildItem -Path $stage -Recurse -Directory -Filter '__pycache__' | Remove-Item -Recurse -Force

if (Test-Path $ZipPath) { Remove-Item -Force $ZipPath }
Compress-Archive -Path $stage -DestinationPath $ZipPath -CompressionLevel Optimal
Remove-Item -Recurse -Force $stage
Write-Host "  已生成: $ZipPath" -ForegroundColor Green

# ── 6. tag + release ──
Write-Step 'Step 6/7 打 tag 并创建 GitHub Release'
if (-not (git tag --list $Tag)) {
    git tag $Tag
}
if (-not (git ls-remote --tags origin $Tag)) {
    git push origin $Tag
    Assert-ExitOk $LASTEXITCODE "推送 tag $Tag 失败"
} else {
    Write-Host "  tag $Tag 已存在于远程，跳过推送" -ForegroundColor Yellow
}

Invoke-Quiet { gh release view $Tag *> $null }
if ($LASTEXITCODE -eq 0) {
    throw "Release $Tag 已存在，如需覆盖请先执行: gh release delete $Tag"
}

gh release create $Tag $ZipPath --generate-notes
Assert-ExitOk $LASTEXITCODE "gh release create $Tag 失败"
Write-Host "  Release: https://github.com/$RepoOwner/$RepoName/releases/tag/$Tag" -ForegroundColor Green

# ── 可选：Windows exe 包 ──
if ($Exe) {
    Write-Step 'Step 7/8 构建 Windows 便携版 exe 包（约 15~30 分钟）'
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Root 'build_exe.ps1') -Version $Version
    Assert-ExitOk $LASTEXITCODE 'build_exe.ps1 失败（构建 exe 包失败）'
    $exeZip = Join-Path $Root "release\ffmpeg-agent-win64-v$Version.zip"
    if (-not (Test-Path $exeZip)) { throw "未找到 exe 包: $exeZip" }
    gh release upload $Tag $exeZip --clobber
    Assert-ExitOk $LASTEXITCODE "上传 exe 包失败"
    Write-Host "  已上传: $exeZip" -ForegroundColor Green
}

# ── Docker（可选）──
if (-not $SkipDocker) {
    Write-Step 'Step 8/8 Docker 构建并推送 GHCR 镜像'
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        Write-Warning '未安装 docker，跳过镜像推送'
    } else {
        Invoke-Quiet { docker info *> $null }
        if ($LASTEXITCODE -ne 0) {
            Write-Warning 'docker 未运行或不可用，跳过镜像推送'
        } else {
            $scopes = Invoke-Quiet { gh auth status --show-token 2>&1 | Out-String }
            if ($scopes -notmatch 'write:packages') {
                Write-Warning "当前 token 缺少 write:packages，可能无法推送镜像，请先运行: gh auth refresh -s write:packages"
            }
            $image = "ghcr.io/$RepoOwner/$RepoName"
            docker build -t "${image}:${Tag}" -t "${image}:latest" .
            Assert-ExitOk $LASTEXITCODE 'docker build 失败'
            $tok = gh auth token
            Assert-ExitOk $LASTEXITCODE '获取 gh token 失败'
            Invoke-Quiet { $tok | docker login ghcr.io -u $RepoOwner --password-stdin *> $null }
            Assert-ExitOk $LASTEXITCODE 'docker login ghcr.io 失败'
            docker push "${image}:${Tag}"
            Assert-ExitOk $LASTEXITCODE "docker push ${image}:${Tag} 失败"
            docker push "${image}:latest"
            Assert-ExitOk $LASTEXITCODE "docker push ${image}:latest 失败"
            Invoke-Quiet { gh api -X PUT "/user/packages/container/$RepoName/visibility" -f visibility=public *> $null }
            Assert-ExitOk $LASTEXITCODE '设置 GHCR 包可见性失败（可手动在 Packages 页面设置公开）'
            Write-Host '  GHCR 镜像已推送并设为公开' -ForegroundColor Green
        }
    }
}

Write-Host "`n完成！" -ForegroundColor Green