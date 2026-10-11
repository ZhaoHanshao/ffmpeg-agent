'use strict'
/**
 * 路径与环境变量解析：Electron 侧与 Python 后端之间的**唯一契约**。
 *
 * 后端（backend/app/main.py）在冻结模式下支持以下环境变量，全部由这里注入：
 *   FFMPEG_AGENT_HOME   可写状态的根目录（日志/上传/产物/向量库/ffmpeg 二进制）
 *   FRONTEND_DIST       静态前端目录（用 Electron 自带那份，改前端无需重跑 PyInstaller）
 *   FFMPEG_BIN_DIR      ffmpeg.exe / ffprobe.exe 所在目录
 *   FFMPEG_AGENT_HOST   监听地址（桌面应用只绑回环）
 *   FFMPEG_AGENT_PORT   监听端口（由 Electron 挑空闲端口，避免固定 8000 冲突）
 *   AUTH_TOKEN          可选访问令牌，由主进程在请求头里注入，页面本身看不到
 */
const fs = require('node:fs')
const path = require('node:path')

const isWindows = process.platform === 'win32'

/** 仓库根目录（dev）。打包后该路径不存在，但打包模式也不会用到它。 */
function repoRoot () {
  return path.resolve(__dirname, '..', '..')
}

function exists (p) {
  try {
    return !!p && fs.existsSync(p)
  } catch {
    return false
  }
}

/**
 * 找到 PyInstaller onedir 产物的可执行文件。
 * 打包后位于 resources/backend/ffmpeg-agent.exe；dev 下回退到 release/dist。
 */
function resolveBundledBackend (app) {
  const override = process.env.FFMPEG_AGENT_BACKEND_EXE
  if (exists(override)) return override

  const exeName = isWindows ? 'ffmpeg-agent.exe' : 'ffmpeg-agent'
  const cands = []
  if (app && app.isPackaged) {
    cands.push(path.join(process.resourcesPath, 'backend', exeName))
  } else {
    cands.push(path.join(repoRoot(), 'release', 'dist', 'ffmpeg-agent', exeName))
  }
  return cands.find(exists) || null
}

/** dev 下用仓库 .venv 里的解释器直接跑源码。 */
function resolveDevPython () {
  const override = process.env.FFMPEG_AGENT_PYTHON
  if (exists(override)) return override
  const cands = isWindows
    ? ['.venv/Scripts/python.exe', '.venv/Scripts/pythonw.exe']
    : ['.venv/bin/python3', '.venv/bin/python']
  for (const rel of cands) {
    const p = path.join(repoRoot(), rel)
    if (exists(p)) return p
  }
  return null
}

/**
 * 计算后端的启动方式。
 * @returns {{mode:'bundled'|'dev', command:string, args:string[], cwd:string,
 *            home:string, detail:string}}
 */
function resolveBackendLaunch (app, homeDir) {
  const bundled = resolveBundledBackend(app)
  if (bundled) {
    return {
      mode: 'bundled',
      command: bundled,
      args: [],
      // PyInstaller 冻结模式会自己 chdir 到 exe 旁边，这里只是给个稳妥的初始值
      cwd: path.dirname(bundled),
      home: homeDir,
      detail: `内置后端 ${bundled}`
    }
  }

  const python = resolveDevPython()
  if (!python) {
    throw new Error(
      '找不到可用的后端：既没有打包产物（release/dist/ffmpeg-agent/ffmpeg-agent.exe），' +
      '也没有仓库内的 .venv。请先运行 build_exe.ps1，或创建 .venv 安装 requirements.txt。'
    )
  }
  const entry = path.join(repoRoot(), 'backend', 'app', 'main.py')
  if (!exists(entry)) throw new Error(`找不到后端入口：${entry}`)
  return {
    mode: 'dev',
    command: python,
    args: [entry],
    cwd: repoRoot(),
    // dev 模式后端不吃 FFMPEG_AGENT_HOME（它用仓库内的相对路径），沿用仓库根目录
    home: repoRoot(),
    detail: `源码后端 ${python} ${entry}`
  }
}

/** 打包模式下前端来自 Electron 的 extraResources；dev 下用仓库构建产物。 */
function resolveFrontendDist (app) {
  const override = process.env.FFMPEG_AGENT_FRONTEND_DIST
  if (exists(override)) return override
  const cands = app && app.isPackaged
    ? [path.join(process.resourcesPath, 'frontend', 'dist')]
    : [path.join(repoRoot(), 'frontend', 'dist')]
  return cands.find((p) => exists(path.join(p, 'index.html'))) || cands[0]
}

/**
 * 组装后端子进程的环境变量。
 * @param {object} opts
 * @param {import('electron').App} opts.app
 * @param {string} opts.home     可写状态根目录
 * @param {number} opts.port
 * @param {string} [opts.authToken]
 */
function buildBackendEnv ({ app, home, port, authToken }) {
  const env = { ...process.env }

  env.FFMPEG_AGENT_HOME = home
  env.FFMPEG_AGENT_HOST = '127.0.0.1'
  env.FFMPEG_AGENT_PORT = String(port)
  env.FRONTEND_DIST = resolveFrontendDist(app)

  env.FFMPEG_BIN_DIR = resolveFfmpegBinDir(app, home)

  if (authToken) env.AUTH_TOKEN = authToken

  // 让 Python 的 stdout/stderr 不被缓冲，日志能实时进 app.log
  env.PYTHONUNBUFFERED = '1'
  env.PYTHONIOENCODING = 'utf-8'
  return env
}

/**
 * ffmpeg / ffprobe 所在目录，优先级从高到低：
 *
 * 1. 打包时随包带上的那份（`-BundleFFmpeg` 会把 exe 放进 resources/backend/backend/bin）。
 *    注意必须**两个都在**才采用：后端的 ensure_ffmpeg_bin 只补缺失的那一个，
 *    而只读目录里补不进去，会直接报下载失败。
 * 2. 用户数据目录（首跑自动下载落在这里，也是始终可写的那个）。
 * 3. dev 下仓库内的 ffmpeg/（省掉一次 100MB 下载；仓库里正好有就顺手用）。
 */
function resolveFfmpegBinDir (app, home) {
  const exeNames = isWindows ? ['ffmpeg.exe', 'ffprobe.exe'] : ['ffmpeg', 'ffprobe']
  const complete = (dir) => exeNames.every((n) => exists(path.join(dir, n)))

  if (app && app.isPackaged) {
    const bundled = path.join(process.resourcesPath, 'backend', 'backend', 'bin')
    if (complete(bundled)) return bundled
  }

  const userBin = path.join(home, 'backend', 'bin')
  if (complete(userBin)) return userBin

  const repoBin = path.join(repoRoot(), 'ffmpeg')
  if (!app || !app.isPackaged) {
    if (complete(repoBin)) return repoBin
  }

  return userBin
}

/** 应用版本：打包后用 Electron 的，dev 下读仓库 VERSION 文件。 */
function readVersion (app) {
  try {
    if (app && app.isPackaged) return app.getVersion()
    const raw = fs.readFileSync(path.join(repoRoot(), 'VERSION'), 'utf8').trim()
    return raw || app.getVersion()
  } catch {
    return app ? app.getVersion() : '0.0.0'
  }
}

module.exports = {
  repoRoot,
  exists,
  resolveBackendLaunch,
  resolveFrontendDist,
  resolveBundledBackend,
  resolveDevPython,
  buildBackendEnv,
  readVersion
}
