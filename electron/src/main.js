'use strict'
/**
 * Electron 主进程。
 *
 * 职责边界（刻意保持很薄）：
 *   1. 拉起 Python 后端（BackendSupervisor），挑一个空闲端口
 *   2. 把窗口指向 http://127.0.0.1:<port>（前端本来就是后端托管的 SPA）
 *   3. 关窗时把后端连同 ffmpeg 孙进程一起收掉
 *
 * 为什么不自己起个 http server 托管前端、再代理 /api：
 *   多一层代理就要自己处理 SSE 流式刷新、multipart 上传、video 的 Range 请求，
 *   任何一处没转发对，问答就会"转圈不吐字"。让 Python 后端直接托管前端，
 *   同源、原生 SSE、原生 Range，全是它已经在 Docker/便携版上跑通的那条路径。
 *   前端产物用 FRONTEND_DIST 指过去，所以改前端只需 npm run build，无需重打 PyInstaller。
 */
const { app, BrowserWindow, Menu, dialog, shell, screen, ipcMain } = require('electron')
const crypto = require('node:crypto')
const fs = require('node:fs')
const path = require('node:path')

const { BackendSupervisor, defaultHome } = require('./backend')
const { readVersion } = require('./config')
const { installAuthHeader } = require('./auth')

const APP_ID = 'com.zhaohanshao.ffmpeg-agent'
const START_TIMEOUT_MS = Number(process.env.FFMPEG_AGENT_START_TIMEOUT_MS || 300000)
const USE_AUTH = process.env.FFMPEG_AGENT_NO_AUTH !== '1'

// 必须在任何 getPath('userData') 之前固定名字，否则打包前后数据目录会不一致
app.setName('ffmpeg-agent')

const state = {
  mainWindow: null,
  splashWindow: null,
  supervisor: null,
  authToken: '',
  shuttingDown: false
}

// ── 窗口位置记忆 ──────────────────────────────────────────────
function stateFile () {
  return path.join(app.getPath('userData'), 'window-state.json')
}

function loadBounds () {
  const fallback = { width: 1360, height: 880 }
  try {
    const raw = JSON.parse(fs.readFileSync(stateFile(), 'utf8'))
    if (raw && Number.isFinite(raw.width) && Number.isFinite(raw.height)) {
      return { width: Math.max(900, raw.width), height: Math.max(600, raw.height), x: raw.x, y: raw.y }
    }
  } catch { /* 首次运行没有这个文件 */ }
  return fallback
}

function saveBounds (win) {
  if (!win || win.isDestroyed() || win.isMinimized()) return
  try {
    const b = win.getNormalBounds()
    fs.mkdirSync(path.dirname(stateFile()), { recursive: true })
    fs.writeFileSync(stateFile(), JSON.stringify(b))
  } catch { /* 记不住窗口位置不影响使用 */ }
}

/** 窗口是否还落在某个屏幕里（拔掉外接屏后别把窗口丢到看不见的地方）。 */
function isOnScreen (bounds, screen) {
  if (!Number.isFinite(bounds.x) || !Number.isFinite(bounds.y)) return false
  return screen.getAllDisplays().some((d) => {
    const w = d.workArea
    return bounds.x < w.x + w.width && bounds.x + bounds.width > w.x &&
           bounds.y < w.y + w.height && bounds.y + bounds.height > w.y
  })
}

// ── 访问令牌 ─────────────────────────────────────────────────
// 具体注入逻辑在 src/auth.js（冒烟测试要复用同一份，别在这儿抄一遍）

// ── 启动画面 ─────────────────────────────────────────────────
function createSplash () {
  const win = new BrowserWindow({
    width: 460,
    height: 300,
    frame: false,
    resizable: false,
    movable: true,
    show: false,
    transparent: true,
    backgroundColor: '#00000000',
    center: true,
    skipTaskbar: true,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true
    }
  })
  win.loadFile(path.join(__dirname, 'splash.html'))
  win.once('ready-to-show', () => win.show())
  win.webContents.on('did-finish-load', () => {
    sendStatus({ status: 'running', progress: 0, step: '正在启动后端服务…' })
  })
  return win
}

function sendStatus (payload) {
  const w = state.splashWindow
  if (w && !w.isDestroyed()) w.webContents.send('backend-status', payload)
}

function closeSplash () {
  const w = state.splashWindow
  state.splashWindow = null
  if (w && !w.isDestroyed()) w.destroy()
}

// ── 主窗口 ───────────────────────────────────────────────────
function createMainWindow (origin) {
  const bounds = loadBounds()
  const options = {
    width: bounds.width,
    height: bounds.height,
    minWidth: 900,
    minHeight: 600,
    show: false,
    backgroundColor: '#f4f5f9', // 与 tokens.css 的 --dsh-bg 一致，避免白闪
    title: 'FFmpeg Agent',
    autoHideMenuBar: false,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      spellcheck: false
    }
  }
  if (isOnScreen(bounds, screen)) {
    options.x = bounds.x
    options.y = bounds.y
  }

  const win = new BrowserWindow(options)
  state.mainWindow = win

  // loadURL 失败会 reject；真正的用户可见处理在下面的 did-fail-load 里，
  // 这里只是别让它变成一条"Unhandled promise rejection"噪音
  win.loadURL(origin).catch(() => {})

  win.once('ready-to-show', () => {
    win.show()
    win.focus()
  })
  win.webContents.once('did-finish-load', closeSplash)

  // 页面加载失败时不能让启动画面永远挂着——那看起来像"卡死"
  win.webContents.on('did-fail-load', (_e, errorCode, errorDescription, validatedURL, isMainFrame) => {
    if (!isMainFrame || errorCode === -3) return // -3 = ERR_ABORTED，用户主动导航中断
    closeSplash()
    const choice = askWithButtons(win, {
      type: 'error',
      title: '界面加载失败',
      message: `无法加载界面（${errorCode} ${errorDescription}）。`,
      detail: `地址：${validatedURL}\n后端：${state.supervisor ? state.supervisor.origin : '未启动'}`,
      buttons: ['重试', '退出'],
      defaultId: 0,
      cancelId: 1
    })
    if (choice === 0) win.loadURL(origin).catch(() => {})
    else app.quit()
  })

  win.on('resize', () => saveBounds(win))
  win.on('move', () => saveBounds(win))
  win.on('close', () => saveBounds(win))
  win.on('closed', () => { state.mainWindow = null })

  // 外链一律交给系统浏览器，不要在应用窗口里打开
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https?:/i.test(url)) shell.openExternal(url)
    return { action: 'deny' }
  })

  // 只允许停留在自己的源上：任何跳转（含前端被注入的链接）都拦下
  win.webContents.on('will-navigate', (event, url) => {
    if (!url.startsWith(origin)) {
      event.preventDefault()
      if (/^https?:/i.test(url)) shell.openExternal(url)
    }
  })

  win.webContents.on('render-process-gone', (_e, details) => {
    const choice = askWithButtons(win, {
      type: 'error',
      title: '界面进程异常退出',
      message: `界面渲染进程已退出（${details.reason}）。`,
      buttons: ['重新加载', '退出'],
      defaultId: 0,
      cancelId: 1
    })
    if (choice === 0 && !win.isDestroyed()) win.reload()
    else app.quit()
  })

  return win
}

// ── 应用菜单 ─────────────────────────────────────────────────
function buildMenu () {
  const dev = !app.isPackaged
  const template = [
    {
      label: '文件',
      submenu: [
        { label: '打开数据目录', click: () => openDataDir() },
        { label: '打开日志文件', click: () => openLogFile() },
        { type: 'separator' },
        { label: '退出', role: 'quit' }
      ]
    },
    {
      label: '视图',
      submenu: [
        { label: '重新加载', accelerator: 'CmdOrCtrl+R', role: 'reload' },
        { label: '强制重新加载', accelerator: 'CmdOrCtrl+Shift+R', role: 'forceReload' },
        { type: 'separator' },
        { label: '实际大小', role: 'resetZoom' },
        { label: '放大', role: 'zoomIn' },
        { label: '缩小', role: 'zoomOut' },
        { type: 'separator' },
        { label: '全屏', role: 'togglefullscreen' },
        ...(dev ? [{ label: '开发者工具', accelerator: 'F12', role: 'toggleDevTools' }] : [])
      ]
    },
    {
      label: '帮助',
      submenu: [
        { label: '关于 FFmpeg Agent', click: () => showAbout() },
        { label: '项目主页', click: () => shell.openExternal('https://github.com/ZhaoHanshao/ffmpeg-agent') }
      ]
    }
  ]
  Menu.setApplicationMenu(Menu.buildFromTemplate(template))
}

function openDataDir () {
  const home = defaultHome(app)
  fs.mkdirSync(home, { recursive: true })
  shell.openPath(home)
}

function openLogFile () {
  const home = defaultHome(app)
  const cands = [
    path.join(home, 'backend', 'logs', 'app.log'),
    path.join(app.getPath('userData'), 'logs', 'backend-host.log')
  ]
  const found = cands.find((p) => fs.existsSync(p))
  if (found) shell.showItemInFolder(found)
  else shell.openPath(path.join(app.getPath('userData'), 'logs'))
}

function askWithButtons (win, options) {
  return win && !win.isDestroyed()
    ? dialog.showMessageBoxSync(win, options)
    : dialog.showMessageBoxSync(options)
}

function showAbout () {
  askWithButtons(state.mainWindow, {
    type: 'info',
    title: '关于',
    message: `FFmpeg Agent ${readVersion(app)}`,
    detail: [
      '用自然语言生成并执行 ffmpeg / ffprobe 命令。',
      '',
      `Electron ${process.versions.electron} · Chromium ${process.versions.chrome} · Node ${process.versions.node}`,
      state.supervisor && state.supervisor.origin ? `后端 ${state.supervisor.origin}` : '后端未运行',
      `数据目录 ${defaultHome(app)}`
    ].join('\n'),
    buttons: ['确定']
  })
}

// ── IPC ──────────────────────────────────────────────────────
ipcMain.handle('app:info', () => ({
  version: readVersion(app),
  packaged: app.isPackaged,
  origin: state.supervisor ? state.supervisor.origin : null,
  home: defaultHome(app),
  logFile: path.join(defaultHome(app), 'backend', 'logs', 'app.log'),
  backendMode: state.supervisor ? state.supervisor.launchMode : null
}))
ipcMain.handle('app:open-data-dir', () => { openDataDir() })
ipcMain.handle('app:open-logs', () => { openLogFile() })

// ── 启动流程 ─────────────────────────────────────────────────
async function bootstrap () {
  app.setAppUserModelId(APP_ID)
  buildMenu()

  const home = defaultHome(app)
  const logDir = path.join(app.getPath('userData'), 'logs')
  fs.mkdirSync(logDir, { recursive: true })
  fs.mkdirSync(home, { recursive: true })

  state.authToken = USE_AUTH ? crypto.randomBytes(24).toString('hex') : ''
  state.splashWindow = createSplash()

  state.supervisor = new BackendSupervisor({ app, home, logDir, authToken: state.authToken })

  state.supervisor.on('exit', ({ code, signal }) => {
    if (state.shuttingDown) return
    // 后端在半路挂掉：窗口里只会显示网络错误，必须明确告诉用户发生了什么
    closeSplash()
    const choice = askWithButtons(state.mainWindow, {
      type: 'error',
      title: '后端服务已停止',
      message: `后端进程意外退出（code=${code} signal=${signal}）。`,
      detail: `可以查看日志了解原因：\n${path.join(home, 'backend', 'logs', 'app.log')}`,
      buttons: ['退出', '打开日志文件夹'],
      defaultId: 0,
      cancelId: 0
    })
    if (choice === 1) openLogFile()
    app.quit()
  })

  let origin
  try {
    origin = await state.supervisor.start(
      (s) => sendStatus(s),
      START_TIMEOUT_MS
    )
  } catch (err) {
    closeSplash()
    const logFile = path.join(home, 'backend', 'logs', 'app.log')
    const choice = askWithButtons(null, {
      type: 'error',
      title: '启动失败',
      message: '后端服务未能启动。',
      detail: `${err.message}\n\n日志：\n${logFile}`,
      buttons: ['退出', '打开日志文件夹'],
      defaultId: 0,
      cancelId: 0
    })
    if (choice === 1) openLogFile()
    await state.supervisor.stop()
    state.shuttingDown = true
    app.quit()
    return
  }

  installAuthHeader(state.supervisor.port, state.authToken)
  createMainWindow(origin)
}

// ── 生命周期 ─────────────────────────────────────────────────
if (!app.requestSingleInstanceLock()) {
  app.quit()
} else {
  app.on('second-instance', () => {
    const win = state.mainWindow
    if (win && !win.isDestroyed()) {
      if (win.isMinimized()) win.restore()
      win.show()
      win.focus()
    }
  })

  app.whenReady().then(bootstrap).catch((err) => {
    dialog.showErrorBox('启动失败', String((err && err.stack) || err))
    app.quit()
  })
}

app.on('window-all-closed', () => {
  app.quit()
})

// 退出前必须把后端（及其 ffmpeg 孙进程）收掉：这里 preventDefault 一次做异步清理，
// 清理完再 app.quit()，此时 shuttingDown 已置位，不会再次拦截。
app.on('before-quit', (event) => {
  if (state.shuttingDown) return
  const sup = state.supervisor
  if (!sup) {
    state.shuttingDown = true
    return
  }
  event.preventDefault()
  state.shuttingDown = true
  sup.stop().catch(() => {}).finally(() => app.quit())
})

// 未捕获异常也要保证不留孤儿进程
process.on('uncaughtException', (err) => {
  try {
    const dir = path.join(app.getPath('userData'), 'logs')
    fs.mkdirSync(dir, { recursive: true })
    fs.appendFileSync(path.join(dir, 'main-error.log'), `${new Date().toISOString()} ${err.stack}\n`)
  } catch { /* ignore */ }
  if (state.supervisor) state.supervisor.stop().catch(() => {}).finally(() => process.exit(1))
  else process.exit(1)
})
