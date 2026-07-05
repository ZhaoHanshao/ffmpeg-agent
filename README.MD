# FFmpeg Agent — 用自然语言处理音视频

> 说人话，就能处理媒体文件。上传图片/视频/音频，告诉 AI 你想做什么，它会自动查找 FFmpeg 命令、执行并返回结果。

---

## 它能做什么？

| 你想做的事 | 只需输入 |
|---|---|
| 把图片颜色反转 | `把这张图片反色` |
| 把视频转成 MP4 | `转为 mp4 格式` |
| 裁剪视频中间 10 秒 | `裁剪中间 10 秒，从第 5 秒开始` |
| 压缩图片到 50% 质量 | `把图片压缩到 50% 质量` |
| 提取视频中的音频 | `提取音频，输出为 mp3` |
| 给视频添加字幕文件 | `把字幕加到视频里` |
| 把图片序列合成视频 | `把这些图片合成视频` |
| 调整视频分辨率 | `缩放到 1280x720` |

> 背后使用的是 **FFmpeg**。AI Agent 会检索 FFmpeg 知识库 → 生成正确命令 → 自动执行 → 流式返回结果。

---

## 技术栈

| 层级 | 技术 |
|---|---|
| 后端框架 | Python FastAPI |
| AI Agent | LangChain + LangGraph（状态图工作流） |
| 向量数据库 | ChromaDB（FFmpeg 官方文档片段） |
| 嵌入模型 | BAAI/bge-small-zh-v1.5（本地运行） |
| 大模型 | OpenAI 兼容接口（GPT、DeepSeek、智谱等） |
| 前端 | Vue 3 + Vite |
| 通信 | SSE（Server-Sent Events）流式输出 |

### 工作流程

```
用户输入 → [Search Agent] 查询 ChromaDB 获取 FFmpeg 文档
         → [Execute Agent] 生成并执行 ffmpeg 命令
         → [Chat Agent] 用自然语言回复结果
         → 流式输出到前端
```

---

## 快速上手

### 1. 环境要求

- Python 3.10+
- Node.js 18+
- FFmpeg（已安装并在 PATH 中可用）
- 一个 OpenAI 兼容的大模型密钥（如 OpenAI、DeepSeek 等）

### 2. 安装后端

```shell
# 克隆项目
git clone <仓库地址>
cd ffmpeg-agent

# 安装 Python 依赖
pip install -r requirements.txt

# 安装 FFmpeg（如果还没有）
# Windows: winget install "FFmpeg (Essentials Build)"
# macOS: brew install ffmpeg
# Ubuntu: sudo apt install ffmpeg
```

### 3. 配置环境变量

复制 `.env.example` 为 `.env`，填写你的配置：

```ini
# 大模型配置（OpenAI 兼容接口）
MODEL_NAME="gpt-4o-mini"
API_KEY="你的OpenAI API密钥"
BASE_URL="https://api.openai.com/v1"

# 文件目录（通常无需修改）
UPLOAD = "backend/upload"
DOWNLOAD = "backend/download"

# 向量数据库（通常无需修改）
DB_DIR = 'backend/data/chroma_db'
COLLECTION_NAME = "ffmpeg_docs"

# 模型下载（首次启动时自动下载）
DOC_URL = "https://ffmpeg.org/ffmpeg-all.html"
BGE_MODEL_NAME = "BAAI/bge-small-zh-v1.5"
BGE_CACHE_DIR = "backend/data/bge_small"
# HF_ENDPOINT = "https://hf-mirror.com"   # 国内 HuggingFace 镜像（如需要）
```

### 4. 启动后端

```shell
python backend/app/main.py
```

**首次启动**会自动执行以下操作（只需等待几分钟）：
1. 从 HuggingFace 下载嵌入模型 `BAAI/bge-small-zh-v1.5`（~200MB）
2. 从 FFmpeg 官网获取文档，分块后构建 ChromaDB 向量库
3. 之后启动会直接加载缓存，速度很快

后端运行在 `http://localhost:8000`

### 5. 安装并启动前端

```shell
cd frontend
npm install
npm run dev
```

前端运行在 `http://localhost:5173`，会自动代理 API 请求到后端。

### 6. 打开浏览器访问

```
http://localhost:5173
```

---

## 使用指南

### 第一步：上传文件

- **拖拽** 文件到左侧虚线框，或 **点击** 选择文件
- 支持任意格式（图片、视频、音频均可）
- 多次上传的文件会**累积**，不会自动删除
- 可在左侧列表中对单个文件进行下载或删除

### 第二步：输入需求

在右侧底部的输入框中，用中文描述你想对文件做什么：

```
把图片颜色反转
```

或更复杂的指令：

```
把视频裁剪成正方形，然后转为 GIF
```

### 第三步：查看结果

- AI 的处理过程会流式输出在聊天区域
- 处理完成后，如果有输出文件会显示在对话框中
  - **图片** 会直接预览
  - **视频** 会嵌入播放器
- 输出文件也会出现在左侧"已完成文件"列表中，可勾选后批量下载（ZIP）或批量删除

---

## 常见问题

**Q：支持哪些大模型？**
任何兼容 OpenAI API 的模型都可以，在 `.env` 中配置 `BASE_URL` 和 `API_KEY` 即可。例如 OpenAI、DeepSeek、智谱 AI、通义千问等。

**Q：知识库里有什么内容？**
知识库存放的是 FFmpeg 官方文档（`ffmpeg-all.html`）的中英文片段。首次启动时自动从官网下载并构建向量库。

**Q：嵌入模型是本地运行的吗？**
是的。使用 `BAAI/bge-small-zh-v1.5`，首次启动时下载到本地（约 200MB），之后离线使用，不依赖第三方 API。

**Q：执行过程安全吗？**
工具层做了严格限制：只能执行 `ffmpeg` 开头的命令，其他命令（`rm`、`sudo`、`python` 等）会被拒绝。

**Q：处理大文件会卡死吗？**
FFmpeg 命令是同步执行的，大文件处理期间服务器会阻塞。建议处理适当大小的文件。

**Q：上传的文件会一直保留吗？**
上传的文件会累积在 `upload/` 目录中，服务器重启时会被清空。你也可以在左侧列表中手动删除不需要的文件。
