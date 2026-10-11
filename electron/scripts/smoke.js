'use strict'
/**
 * Electron 冒烟测试：把"主进程 ↔ Python 后端 ↔ 前端页面"这条链路整条跑一遍。
 *
 * 用例（每条都打一行 PASS/FAIL，最后给出退出码）：
 *   1. 后端进程能被拉起，/api/health 开始应答
 *   2. 后端托管的前端页面可访问，且是真正的 SPA 入口（含 #app 挂载点）
 *   3. 访问令牌生效：不带头的 /api/* 被拒（401），主进程注入的头能通过（200）
 *   4. BrowserWindow 能真正渲染页面（DOM 就绪、预加载桥接可用、无控制台报错）
 *   5. 截图留证；关闭窗口后后端进程确实退出（不留孤儿）
 *
 * 用法（在 electron/ 目录下）：
 *   npx electron scripts/smoke.js              # 有 PyInstaller 产物就用产物
 *   npx electron scripts/smoke.js --dev        # 强制用仓库 .venv 跑源码
 *   npx electron scripts/smoke.js --keep       # 保留后端进程，便于手动排查
 *   npx electron scripts/smoke.js --shot out.png
 */
const { app, BrowserWindow } = require('electron')
const crypto = require('node:crypto')
const fs = require('node:fs')
const http = require('node:http')
const path = require('node:path')

const { BackendSupervisor, defaultHome, probeHealth } = require('../src/backend')
const { repoRoot } = require('../src/config')
const { installAuthHeader } = require('../src/auth')

const argv = process.argv.slice(2)
const FORCE_DEV = argv.includes('--dev')
const KEEP = argv.includes('--keep')
const shotArg = argv.indexOf('--shot')
const SHOT_DIR = path.join(repoRoot(), 'release', 'electron-smoke')
const SHOT_PATH = shotArg >= 0 && argv[shotArg + 1]
  ? path.resolve(argv[shotArg + 1])
  : path.join(SHOT_DIR, 'app.png')

// 与 main.js 保持一致：否则冒烟测试用的是 %APPDATA%\Electron，
// 和真实应用的数据目录不是一个，测出来的东西不算数
app.setName('ffmpeg-agent')

let failures = 0
let checks = 0
let keepAlive = false

function check (name, ok, detail) {
  checks += 1
  if (ok) {
    console.log(`PASS  ${name}${detail ? `  — ${detail}` : ''}`)
  } else {
    failures += 1
    console.log(`FAIL  ${name}${detail ? `  — ${detail}` : ''}`)
  }
}

/** 极简 HTTP 客户端：可控地带上/不带令牌。 */
function request (port, urlPath, { token, timeout = 15000, method = 'GET', body, contentType } = {}) {
  return new Promise((resolve, reject) => {
    const headers = {}
    if (token) headers['X-Auth-Token'] = token
    if (body !== undefined) headers['Content-Type'] = contentType || 'application/json'
    const req = http.request(
      { host: '127.0.0.1', port, path: urlPath, headers, timeout, method },
      (res) => {
        let text = ''
        res.setEncoding('utf8')
        res.on('data', (c) => { text += c })
        res.on('end', () => resolve({ status: res.statusCode, body: text, headers: res.headers }))
      }
    )
    req.on('timeout', () => { req.destroy(new Error(`请求 ${urlPath} 超时`)) })
    req.on('error', reject)
    if (body !== undefined) req.write(body)
    req.end()
  })
}

/** 等一个条件成立，超时返回 false。 */
async function waitFor (label, fn, timeoutMs = 90000) {
  const deadline = Date.now() + timeoutMs
  let last = null
  for (;;) {
    try {
      const v = await fn()
      if (v) return true
      last = v
    } catch (e) {
      last = e.message
    }
    if (Date.now() > deadline) {
      console.log(`      （等待「${label}」超时，最后状态：${last}）`)
      return false
    }
    await new Promise((r) => setTimeout(r, 500))
  }
}

async function run () {
  const home = defaultHome(app)
  const logDir = path.join(app.getPath('userData'), 'logs')
  const token = crypto.randomBytes(24).toString('hex')

  console.log('── FFmpeg Agent Electron 冒烟测试 ──')
  console.log(`可写根目录 : ${home}`)
  console.log(`应用日志   : ${path.join(logDir, 'backend-host.log')}`)
  console.log('')

  // 强制 dev 模式：把内置产物路径指到不存在的位置，逼 config 走 .venv 分支
  if (FORCE_DEV) process.env.FFMPEG_AGENT_BACKEND_EXE = path.join(home, '__no_such_backend__.exe')

  const sup = new BackendSupervisor({ app, home, logDir, authToken: token })

  // ── 1. 启动后端 ──
  const statuses = []
  sup.on('exit', (info) => { console.log(`      （后端提前退出 code=${info.code}）`) })
  const t0 = Date.now()
  const origin = await sup.start((s) => {
    statuses.push(s)
    console.log(`      进度 ${s.progress}% ${s.step}`)
  }, 240000)
  const bootMs = Date.now() - t0
  check('后端启动并开始应答 /api/health', !!origin, `${origin}，耗时 ${(bootMs / 1000).toFixed(1)}s`)
  check('后端模式', !!sup.launchMode, sup.launchMode)
  check('启动过程有进度事件上报', statuses.length > 0, `${statuses.length} 条`)

  // 冻结模式下后端默认把日志/上传/产物写在 exe 旁边，装到 Program Files 后那是只读的。
  // FFMPEG_AGENT_HOME 是否真被采纳，看它有没有在可写根下建出目录最直接。
  if (sup.launchMode === 'bundled') {
    const homeLog = path.join(home, 'backend', 'logs', 'app.log')
    const okHome = await waitFor('后端在可写根下落盘', () => fs.existsSync(homeLog), 30000)
    check('FFMPEG_AGENT_HOME 生效（后端写在用户数据目录而非 exe 旁）', okHome, homeLog)
    const exeDirLog = path.join(repoRoot(), 'release', 'dist', 'ffmpeg-agent', 'backend', 'logs', 'app.log')
    const exeMtime = fs.existsSync(exeDirLog) ? fs.statSync(exeDirLog).mtimeMs : 0
    check('没有往 exe 旁的只读位置写日志', exeMtime < t0, exeMtime ? `exe 旁日志 mtime=${new Date(exeMtime).toISOString()}` : 'exe 旁无日志')
  }

  // ── 2. 健康检查 & 静态页面 ──
  const health = await request(sup.port, '/api/health')
  check('/api/health 返回 200', health.status === 200, `status=${health.status}`)
  let healthBody = {}
  try { healthBody = JSON.parse(health.body) } catch { /* 下面会判 */ }
  check('/api/health 是合法 JSON 且带 status 字段',
    typeof healthBody.status === 'string', `status=${healthBody.status}`)

  const index = await request(sup.port, '/')
  check('前端页面可访问（GET /）', index.status === 200, `status=${index.status}`)
  check('返回的是 SPA 入口', /id="app"/.test(index.body), `${index.body.length} 字节`)

  // 这一条是 FRONTEND_DIST 覆盖的**真验证**：内置产物里的 index.html 引的是旧哈希，
  // 只有真正托管了仓库那份 frontend/dist，资源名才对得上。
  const localIndex = fs.readFileSync(path.join(repoRoot(), 'frontend', 'dist', 'index.html'), 'utf8')
  const localAsset = (localIndex.match(/assets\/index-[\w-]+\.js/) || [])[0]
  check('托管的是 Electron 指定的那份前端（FRONTEND_DIST 覆盖生效）',
    !!localAsset && index.body.includes(localAsset),
    `期望含 ${localAsset || '(未能在 frontend/dist/index.html 里找到资源名)'}`)

  const servedAsset = (index.body.match(/assets\/index-[\w-]+\.js/) || [])[0]
  if (servedAsset) {
    const asset = await request(sup.port, '/' + servedAsset)
    check('静态资源本身可访问', asset.status === 200, `${servedAsset} → ${asset.status}`)
  }

  // ── 3. 访问令牌 ──
  const noToken = await request(sup.port, '/api/conversations')
  check('不带令牌访问 /api/* 被拒', noToken.status === 401, `status=${noToken.status}`)
  const withToken = await request(sup.port, '/api/conversations', { token })
  check('带令牌访问 /api/* 通过', withToken.status === 200, `status=${withToken.status}`)

  // 用户数据必须落在可写根下。SETTINGS_FILE / CONVERSATIONS_DIR 的默认值是**相对路径**，
  // 不跟着 FFMPEG_AGENT_HOME 走的话会解析到安装目录里：配置和对话历史重装即丢，
  // 装到 Program Files 时更是保存静默失效。曾经就漏了这两个，这里钉住。
  const created = await request(sup.port, '/api/conversations', {
    token, method: 'POST', body: JSON.stringify({ mode: 'ffmpeg', title: '冒烟测试' })
  })
  check('能创建对话', created.status === 200, `status=${created.status}`)
  const convDir = path.join(home, 'backend', 'data', 'conversations')
  check('对话落盘在可写根下（CONVERSATIONS_DIR 生效）',
    await waitFor('对话文件出现', () => fs.existsSync(convDir) && fs.readdirSync(convDir).length > 0, 15000),
    convDir)
  const wrongConvDir = path.join(repoRoot(), 'release', 'dist', 'ffmpeg-agent', 'backend', 'data', 'conversations')
  check('没有把对话写进安装目录（相对路径没被误解析）', !fs.existsSync(wrongConvDir), wrongConvDir)

  // ── 4. 真正渲染页面 ──
  // 必须和 main.js 一样在窗口发起请求之前装好头注入，否则这里测不到真实行为
  installAuthHeader(sup.port, token)

  const consoleErrors = []
  const win = new BrowserWindow({
    width: 1360,
    height: 880,
    show: false,
    backgroundColor: '#f4f5f9',
    webPreferences: {
      preload: path.join(__dirname, '..', 'src', 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true
    }
  })
  win.webContents.on('console-message', (...args) => {
    // Electron 35 起改成一个事件对象（level 是 'error' 之类的字符串），
    // 老签名是 (event, level, message, line, sourceId)。两种都认。
    const first = args[0]
    const structured = first && typeof first === 'object' && 'level' in first
    const level = structured ? first.level : args[1]
    const message = structured ? first.message : args[2]
    if (level === 'error' || level === 3) consoleErrors.push(String(message).slice(0, 200))
  })
  win.webContents.on('did-fail-load', (_e, code, desc) => {
    console.log(`      页面加载失败 ${code} ${desc}`)
  })

  await win.loadURL(origin)

  // 主进程注入的请求头对渲染进程发起的请求也生效 → 页面能自己拉到数据
  const rendererOk = await waitFor('页面完成初始化', async () => {
    const r = await win.webContents.executeJavaScript(`(async () => {
      const res = await fetch('/api/conversations')
      return { status: res.status, host: !!(window.agentHost && window.agentHost.isElectron) }
    })()`)
    return r && r.status === 200 && r.host ? r : false
  }, 60000)
  check('渲染进程的 fetch 通过主进程注入的令牌（200）且预加载桥接可用', rendererOk)

  const dom = await win.webContents.executeJavaScript(`(() => ({
    title: document.title,
    hasApp: !!document.querySelector('#app'),
    appChildren: (document.querySelector('#app') || {}).childElementCount || 0,
    text: (document.body.innerText || '').slice(0, 200)
  }))()`)
  check('页面标题正确', dom.title === 'FFmpeg Agent' || /ffmpeg/i.test(dom.title), dom.title)
  check('#app 已挂载出内容', dom.hasApp && dom.appChildren > 0, `子节点 ${dom.appChildren} 个`)
  console.log(`      首屏文字：${dom.text.replace(/\s+/g, ' ').slice(0, 120)}`)

  // 等初始化横幅结束（ok / error 都算到达终态）
  const settled = await waitFor('后端初始化到达终态', async () => {
    const h = await probeHealth(sup.port)
    return h.up && h.body && h.body.status && h.body.status !== 'running' ? h.body : false
  }, 240000)
  check('后端初始化到达终态（ok/error）', settled, settled ? JSON.stringify(settled).slice(0, 160) : '')
  if (settled && settled.status === 'error') {
    console.log(`      初始化错误：${String(settled.error).split('\n').slice(-4).join(' | ')}`)
  }

  await new Promise((r) => setTimeout(r, 1500))
  fs.mkdirSync(path.dirname(SHOT_PATH), { recursive: true })
  let image = await win.webContents.capturePage()
  if (image.isEmpty()) {
    // 隐藏窗口在某些显卡驱动下不合成画面：先显示（不抢焦点）再抓一次
    win.showInactive()
    await new Promise((r) => setTimeout(r, 1200))
    image = await win.webContents.capturePage()
  }
  fs.writeFileSync(SHOT_PATH, image.toPNG())
  check('已截图留证', fs.existsSync(SHOT_PATH) && fs.statSync(SHOT_PATH).size > 5000,
    `${SHOT_PATH}（${fs.statSync(SHOT_PATH).size} 字节）`)

  const realErrors = consoleErrors.filter((m) => !/favicon|ERR_/i.test(m))
  check('页面没有控制台错误', realErrors.length === 0, realErrors.slice(0, 3).join(' | '))

  // ── 5. 收尾：进程必须真的退出 ──
  if (KEEP) {
    keepAlive = true
    console.log('\n--keep：保留后端进程与窗口，测试结束但不会退出 Electron。')
    return
  }
  win.destroy()
  await sup.stop()
  const stillUp = await probeHealth(sup.port, 1500)
  check('关闭后后端端口不再应答（无孤儿进程）', !stillUp.up)

  // 顺带确认没有再监听这个端口
  check('后端进程句柄已清空', sup.child === null)
}

app.whenReady()
  .then(run)
  .catch((err) => {
    failures += 1
    console.log(`FAIL  冒烟测试异常终止 — ${(err && err.stack) || err}`)
  })
  .finally(() => {
    console.log(`\n结果：${checks - failures}/${checks} 项通过`)
    if (SHOT_PATH && fs.existsSync(SHOT_PATH)) console.log(`截图：${SHOT_PATH}`)
    if (keepAlive) return
    // 不用 app.exit(0) 一刀切，让 Electron 走正常退出，能暴露"退不干净"的问题
    setTimeout(() => app.exit(failures === 0 ? 0 : 1), 300)
  })
