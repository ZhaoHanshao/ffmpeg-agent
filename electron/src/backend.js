'use strict'
/**
 * Python 后端进程的看护者：挑端口 → 启动 → 等 /api/health → 退出时连子进程一起收掉。
 *
 * 为什么不把后端塞进 Electron 主进程：后端是一整套 FastAPI + LangGraph + ChromaDB，
 * 需要独立的 GIL、独立的内存与崩溃边界；而且已有的 PyInstaller 产物可以直接复用。
 * Electron 只负责“把它拉起来、把窗口指过去、关窗时别留孤儿进程”。
 */
const { EventEmitter } = require('node:events')
const { spawn } = require('node:child_process')
const fs = require('node:fs')
const http = require('node:http')
const net = require('node:net')
const path = require('node:path')

const { buildBackendEnv, resolveBackendLaunch } = require('./config')

const HEALTH_POLL_INTERVAL_MS = 600

/** 让操作系统分配一个空闲端口，再立刻释放给后端用。 */
function findFreePort () {
  return new Promise((resolve, reject) => {
    const srv = net.createServer()
    srv.unref()
    srv.on('error', reject)
    srv.listen(0, '127.0.0.1', () => {
      const { port } = srv.address()
      srv.close(() => resolve(port))
    })
  })
}

/** GET /api/health —— 任何 HTTP 响应（含 500）都算“服务已起来”。 */
function probeHealth (port, timeoutMs = 2000) {
  return new Promise((resolve) => {
    const req = http.get(
      { host: '127.0.0.1', port, path: '/api/health', timeout: timeoutMs },
      (res) => {
        let body = ''
        res.setEncoding('utf8')
        res.on('data', (c) => { body += c })
        res.on('end', () => {
          let parsed = null
          try { parsed = JSON.parse(body) } catch { /* 非 JSON 也算活着 */ }
          resolve({ up: true, statusCode: res.statusCode, body: parsed })
        })
      }
    )
    req.on('timeout', () => { req.destroy(); resolve({ up: false }) })
    req.on('error', () => resolve({ up: false }))
  })
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

class BackendSupervisor extends EventEmitter {
  /**
   * @param {object} opts
   * @param {import('electron').App} opts.app
   * @param {string} opts.home     可写状态根目录
   * @param {string} opts.logDir   Electron 侧日志目录
   * @param {string} [opts.authToken]
   */
  constructor ({ app, home, logDir, authToken }) {
    super()
    this.app = app
    this.home = home
    this.logDir = logDir
    this.authToken = authToken
    this.child = null
    this.port = null
    this.launchMode = null
    this.stopping = false
    this._logStream = null
    this._lastStatusKey = ''
  }

  get origin () {
    return `http://127.0.0.1:${this.port}`
  }

  _openLog () {
    if (this._logStream) return
    try {
      fs.mkdirSync(this.logDir, { recursive: true })
      this._logStream = fs.createWriteStream(
        path.join(this.logDir, 'backend-host.log'), { flags: 'a' }
      )
      this._logStream.write(`\n===== ${new Date().toISOString()} 启动后端 =====\n`)
    } catch {
      this._logStream = null
    }
  }

  _log (line) {
    if (this._logStream) {
      try { this._logStream.write(`${line}\n`) } catch { /* 磁盘问题不该弄崩应用 */ }
    }
    if (!this.app.isPackaged) process.stdout.write(`[backend] ${line}\n`)
  }

  _reportStatus (onStatus, body) {
    if (!onStatus) return
    const status = (body && body.status) || 'running'
    const progress = (body && body.progress) || 0
    const step = (body && body.step) || '正在启动后端…'
    const key = `${status}|${progress}|${step}`
    if (key === this._lastStatusKey) return
    this._lastStatusKey = key
    onStatus({ status, progress, step })
  }

  /**
   * 启动后端并等到它开始应答 HTTP。
   *
   * 只等“端口开始应答”，不等 status=ok：冻结模式下向量库/ffmpeg 准备是在后台线程里做的，
   * web 服务先起来，前端自己的初始化横幅会接着轮询 /api/health 显示进度。
   * dev 模式（未冻结）预加载是同步的，会卡住 lifespan，所以这里的等待就成了启动画面的全部意义。
   *
   * @param {(s:{status:string,progress:number,step:string})=>void} [onStatus]
   * @param {number} [timeoutMs]
   */
  async start (onStatus, timeoutMs = 180000) {
    if (this.child) return this.origin
    this.stopping = false

    const launch = resolveBackendLaunch(this.app, this.home)
    this.launchMode = launch.mode
    this.port = await findFreePort()
    this._openLog()
    this._log(`模式=${launch.mode} 端口=${this.port} 可写根=${launch.home}`)
    this._log(launch.detail)

    const env = buildBackendEnv({
      app: this.app,
      home: launch.home,
      port: this.port,
      authToken: this.authToken
    })
    this._log(`FRONTEND_DIST=${env.FRONTEND_DIST}`)
    this._log(`FFMPEG_BIN_DIR=${env.FFMPEG_BIN_DIR}`)

    this.child = spawn(launch.command, launch.args, {
      cwd: launch.cwd,
      env,
      windowsHide: true,
      stdio: ['ignore', 'pipe', 'pipe']
    })

    this.child.stdout.setEncoding('utf8')
    this.child.stderr.setEncoding('utf8')
    this.child.stdout.on('data', (d) => this._log(String(d).replace(/\s+$/, '')))
    this.child.stderr.on('data', (d) => this._log(String(d).replace(/\s+$/, '')))

    this.child.on('error', (err) => {
      this._log(`启动失败：${err.message}`)
    })

    this.child.on('exit', (code, signal) => {
      this._log(`后端进程退出 code=${code} signal=${signal}`)
      const unexpected = !this.stopping
      this.child = null
      if (unexpected) this.emit('exit', { code, signal })
    })

    await this.waitUntilUp(onStatus, timeoutMs)
    return this.origin
  }

  /** 轮询 /api/health 直到服务开始应答（或进程提前退出 / 超时）。 */
  async waitUntilUp (onStatus, timeoutMs = 180000) {
    const deadline = Date.now() + timeoutMs
    for (;;) {
      if (!this.child) throw new Error('后端进程已退出，未能启动服务')
      const res = await probeHealth(this.port)
      if (res.up) {
        this._reportStatus(onStatus, res.body)
        return
      }
      if (Date.now() > deadline) {
        throw new Error(`等待后端启动超时（${Math.round(timeoutMs / 1000)} 秒）`)
      }
      await sleep(HEALTH_POLL_INTERVAL_MS)
    }
  }

  /** 结束后端，连同它派生的 ffmpeg/ffprobe 子进程一起。 */
  async stop () {
    const child = this.child
    this.stopping = true
    if (!child) {
      this._closeLog()
      return
    }
    const pid = child.pid
    this._log(`正在停止后端 pid=${pid}`)

    await new Promise((resolve) => {
      let done = false
      const finish = () => { if (!done) { done = true; resolve() } }
      child.once('exit', finish)

      if (process.platform === 'win32') {
        // taskkill /T 才能带走 ffmpeg 孙进程；直接 child.kill() 会留下正在转码的进程
        try {
          spawn('taskkill', ['/PID', String(pid), '/T', '/F'], { windowsHide: true })
            .on('exit', finish)
            .on('error', () => { try { child.kill('SIGKILL') } catch { /* 已退出 */ } finish() })
        } catch {
          try { child.kill('SIGKILL') } catch { /* 已退出 */ }
          finish()
        }
      } else {
        try { child.kill('SIGTERM') } catch { finish() }
      }

      // 兜底：5 秒还没收干净就强杀，绝不阻塞退出流程
      setTimeout(() => {
        try { child.kill('SIGKILL') } catch { /* 已退出 */ }
        finish()
      }, 5000)
    })

    this.child = null
    this._log(`后端已停止（${new Date().toISOString()}）`)
    this._closeLog()
  }

  _closeLog () {
    if (this._logStream) {
      try { this._logStream.end() } catch { /* ignore */ }
    }
    this._logStream = null
  }
}

/** 用户数据目录下的可写根：%APPDATA%\<应用名>\state。 */
function defaultHome (app) {
  const override = process.env.FFMPEG_AGENT_HOME
  if (override) return override
  return path.join(app.getPath('userData'), 'state')
}

module.exports = { BackendSupervisor, defaultHome, findFreePort, probeHealth }
