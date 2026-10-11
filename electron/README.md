# FFmpeg Agent · Electron 桌面外壳

把 ffmpeg-agent 包成 Windows 桌面应用：Electron 负责窗口，Python 后端（PyInstaller 产物）
负责一切业务逻辑。用户不需要装 Python、Node、ffmpeg，双击即用。

```
┌──────────────────────── FFmpeg Agent.exe (Electron) ────────────────────────┐
│  resources/backend/           ← PyInstaller onedir（python314 + 依赖 + 向量库）│
│  resources/frontend/dist/     ← 前端构建产物（后端用 FRONTEND_DIST 指过来）    │
│  resources/app.asar           ← 本目录的 src/*                                │
└──────────────────────────────────────────────────────────────────────────────┘
        │ 启动时 spawn
        ▼
   ffmpeg-agent.exe -- FFMPEG_AGENT_HOME=%APPDATA%\ffmpeg-agent\state
                    -- FFMPEG_AGENT_PORT=<空闲端口>  -- AUTH_TOKEN=<每次随机的令牌>
        │ uvicorn 监听 127.0.0.1:<port>，同时托管前端
        ▼
   BrowserWindow.loadURL("http://127.0.0.1:<port>")
```

## 为什么是这个结构

**窗口指向 `http://127.0.0.1:<port>`，而不是自己起个静态服务器 + 代理 `/api`。**
一旦自己代理，就要手工处理 SSE 的流式刷新、multipart 上传、`<video>` 的 Range 请求，
任何一处没转发对，表现就是「问答转圈不吐字」或「视频拖不动进度条」。
让 Python 后端直接托管前端，同源、原生 SSE、原生 Range——正是它已经在 Docker
和便携版上跑通的那条路径，Electron 只多包了一层窗口。

**命令静默执行，不弹黑窗口。** 打包版是 windowed（自身没有控制台）的 exe，而
ffmpeg/ffprobe 是控制台程序——父进程没有控制台时 Windows 会给子进程**新建**一个
控制台窗口，于是每执行一条命令就弹一个"命令运行窗口"，长转码时那个窗口一直挂着。
后端所有 spawn 点都带 `silent_spawn_kwargs()`（`CREATE_NO_WINDOW` + `SW_HIDE`），
stdout/stderr 走重定向的临时文件，进度与错误捕获不受影响。详见根目录 AGENTS.md。

**可写状态全部落在用户数据目录。** PyInstaller 冻结模式原本把上传/产物/日志/向量库/
ffmpeg 二进制都放在 exe 旁边（`backend/app/main.py` 会 `chdir` 到 exe 所在目录，
所以那些**相对路径**默认值全都解析到安装目录里）。这在便携版 zip 里没问题，
但装到 `Program Files` 后那些目录是只读的，重装/升级还会把用户数据一起抹掉。

所以这里设 `FFMPEG_AGENT_HOME=%APPDATA%\ffmpeg-agent\state`，后端把「可写根」整体挪过去。
**容易漏的是后面这几个**——它们的默认值是相对路径，必须显式跟着 `_home` 走：

| 环境变量 | 默认值（相对） | 漏掉的后果 |
|---|---|---|
| `DB_DIR` | `backend/data/chroma_db` | 向量库复制不进安装目录，启动失败 |
| `UPLOAD` / `DOWNLOAD` | `backend/upload`、`backend/download` | 上传与产物写不进去 |
| `FFMPEG_BIN_DIR` | exe 旁 `backend/bin` | 首跑下载 ffmpeg 失败 |
| `SETTINGS_FILE` | `backend/data/llm_settings.json` | **模型配置丢失**：重装即没，装到 Program Files 保存静默失效 |
| `CONVERSATIONS_DIR` | `backend/data/conversations` | **全部对话历史丢失** |
| `DOC_CACHE_DIR` | `backend/data` | `DOC_REFRESH_DAYS>0` 重抓文档时写进安装目录 |

`SETTINGS_FILE` 与 `CONVERSATIONS_DIR` 就是漏过一次的那两个（现象是用户填好的模型配置
在安装目录里，重装后不见了）。`scripts/smoke.js` 现在有对应断言钉住。

**前端走 `FRONTEND_DIST` 覆盖。** 否则前端产物被打进 `_internal/frontend/dist`，
改一行 CSS 都要重跑一次 20 分钟的 PyInstaller。现在 `npm run build` 之后重新打包即可。

## 目录

| 文件 | 作用 |
|------|------|
| `src/main.js` | 主进程：单实例锁、启动画面、主窗口、菜单、退出时收掉后端 |
| `src/backend.js` | 后端看护：挑空闲端口 → spawn → 轮询 `/api/health` → `taskkill /T` 收进程树 |
| `src/config.js` | 路径与环境变量的**唯一契约**（打包/开发两条分支都在这里判定） |
| `src/preload.js` | `contextBridge` 只暴露只读信息与三个宿主能力，页面拿不到 Node |
| `src/splash.html` | 启动画面（无边框卡片 + 进度条），配色对齐 `frontend/src/styles/tokens.css` |
| `scripts/smoke.js` | 冒烟测试：整条链路跑一遍并截图 |
| `build/make_icon.py` | 生成 `icon.png` / `icon.ico`（需要 Pillow） |

## 开发

```powershell
cd electron
npm install
npm start            # 有 release\dist\ffmpeg-agent 就用它，否则回退到 .venv 跑源码
```

开发时后端数据（上传/产物/日志）仍在 `%APPDATA%\ffmpeg-agent\state` 下，
和安装版共用一份，所以开发时上传的文件在安装版里也看得到。

有用的环境变量：

| 变量 | 作用 |
|------|------|
| `FFMPEG_AGENT_BACKEND_EXE` | 强制指定后端 exe（指向不存在的位置即可逼它走 `.venv` 源码模式） |
| `FFMPEG_AGENT_PYTHON` | 指定 dev 模式用的解释器 |
| `FFMPEG_AGENT_HOME` | 覆盖可写根目录 |
| `FFMPEG_AGENT_FRONTEND_DIST` | 覆盖托管的前端目录（配 `npm run dev` 的产物做联调） |
| `FFMPEG_AGENT_NO_AUTH=1` | 关掉每次随机的访问令牌 |
| `FFMPEG_AGENT_START_TIMEOUT_MS` | 等后端起来的超时，默认 300000 |

## 冒烟测试

```powershell
cd electron
npx electron scripts/smoke.js              # 用 PyInstaller 产物
npx electron scripts/smoke.js --dev        # 用 .venv 跑源码（会慢，首跑要建库）
npx electron scripts/smoke.js --shot out.png
```

它会真的拉起后端、真的在 `BrowserWindow` 里渲染页面，逐条打 `PASS/FAIL`，
并把截图写到 `release/electron-smoke/app.png`。失败时退出码非 0。

## 打包

在仓库根目录：

```powershell
.\build_electron.ps1                       # 全量：前端 + PyInstaller 后端 + 安装包
.\build_electron.ps1 -SkipBackend -DirOnly # 只改过 Electron/前端时的快速回路
.\build_electron.ps1 -Smoke                # 打包前先跑冒烟测试
```

产物在 `release\electron\`：

- `FFmpeg-Agent-<版本>-win-x64-Setup.exe` —— NSIS 安装包（可选安装路径）
- `FFmpeg-Agent-<版本>-win-x64-Portable.exe` —— 免安装单文件（每次启动解压到临时目录）
- `win-unpacked\` —— 未打包目录，直接跑里面的 `FFmpeg Agent.exe` 最快

## 首次运行

首次启动会做两件事，启动画面上都有进度：

1. 把内置的 Chroma 向量库复制到 `%APPDATA%\ffmpeg-agent\state\backend\data\chroma_db`（约 35MB）
2. 下载 ffmpeg / ffprobe 到 `%APPDATA%\ffmpeg-agent\state\backend\bin`（约 100MB）

**想跳过第 2 步**：把 `ffmpeg.exe` 和 `ffprobe.exe` 直接放进
`%APPDATA%\ffmpeg-agent\state\backend\bin\`，程序发现已存在就不会联网下载。

然后到页面右上角 ⚙️ 填模型配置（地址 / 密钥 / 模型名），保存时会做一次连通性探测。

## 访问令牌

主进程每次启动生成一个随机 `AUTH_TOKEN` 交给后端，并在 `webRequest.onBeforeSendHeaders`
里给所有发往 `127.0.0.1:<port>` 的请求补上 `X-Auth-Token`。

好处是页面**完全不需要知道令牌**：前端在 `localStorage` 里没有令牌时会走「直链」，
而直链（`<img>` / `<video>`）同样经过 session，头是主进程补的，所以预览和播放都正常；
同时本机其它进程（或浏览器里发起的跨站请求）即使猜到端口也调不动 `/api/*`。

## 已知边界

- **强杀 Electron 会留下后端进程。** 正常关窗走 `before-quit` → `taskkill /T /F` 收干净
  （实测：关窗后 2 秒内 Electron 退出、后端清零，日志里能看到 `正在停止后端` +
  `后端已停止`）；但用任务管理器杀掉 Electron 主进程时，Python 后端会继续活着
  （端口是随机挑的，不影响下次启动，只是白占内存）。这是 Windows 上父子进程的默认行为，
  没有引入原生 job object 模块去解决。
- **只用 PowerShell 的 `Process.MainWindowHandle` 关窗口会测出假故障。**
  Electron 进程有一堆隐藏的 `Chrome_WidgetWin_0` 辅助窗口，`.NET` 的
  `MainWindowHandle` 有时会挑中它们并**缓存下来**，于是 `CloseMainWindow()` 返回
  `True`、窗口却纹丝不动——现象是"应用没退、后端成孤儿"，看起来像应用有 bug。
  真正的窗口是**可见、有面积、类名为 `Chrome_WidgetWin_1`** 的那个。
  要自动化关窗就自己 `EnumWindows` 找它再 `PostMessage(WM_CLOSE)`，别信 `CloseMainWindow()`。
- **免安装版（portable）的临时解压目录不要复用。** 它解压到 `%TEMP%` 下一个固定路径；
  如果上一次是被强杀的（目录还在、文件可能被占用），下一次跑起来会读到混合内容。
  测试前先清掉那个目录，否则会得到一堆解释不了的怪现象。
- **单实例锁会让"第二个实例"静默退出。** 上一轮没杀干净时，新启动的应用会立刻退出、
  把窗口让给老进程——于是你测的其实是老进程。自动化测试要先记录启动前已有的 pid，
  之后只认新出现的那个。
- **`ELECTRON_RUN_AS_NODE=1`**（部分 CI/agent 环境会设）会让 `electron.exe` 退化成
  普通 Node，`require('electron')` 拿到的是可执行文件路径字符串，报
  `Cannot read properties of undefined (reading 'whenReady')`。跑任何 Electron 命令前先清掉它。
- **只支持 Windows x64。** 图标、`taskkill`、PyInstaller 产物（`.exe` + `_internal`）
  都是按 Windows 来的；要上别的平台得改 `package.json` 的 `build.win` 与
  `config.js` 里那几处 `.exe` 判断。
- **关窗即退出**（`window-all-closed` → `app.quit()`），没有做托盘常驻。
- **首次启动慢**：免安装版每次启动都要把约 645MB 解压到 `%TEMP%`（实测首次约 60 秒）。
  要快就用安装包（只装一次）或直接跑 `win-unpacked\FFmpeg Agent.exe`。
