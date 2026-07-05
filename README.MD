# 🎬 FFmpeg Agent — 用自然语言处理音视频

> **说人话，就能处理媒体文件。** 上传图片/视频/音频，告诉 AI 你想做什么，它会自动查找 FFmpeg 命令、执行并返回结果。

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

> **背后使用的是 FFmpeg**，无论你输入什么需求，AI Agent 会检索 FFmpeg 知识库 → 生成正确命令 → 自动执行 → 返回结果给你。

---

## 快速上手

### 1. 环境要求

- Python 3.10+
- Node.js 18+
- FFmpeg（已安装并在 PATH 中可用）
- 一个兼容 OpenAI API 的大模型密钥（如智谱 AI）

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

复制 `.env` 文件并填写：

```ini
# 大模型配置（兼容 OpenAI 接口）
MODEL_NAME="glm-4.5-air"
API_KEY="你的API密钥"
BASE_URL="https://open.bigmodel.cn/api/paas/v4"

# 文件目录（通常无需修改）
UPLOAD = "backend/upload"
DOWNLOAD = "backend/download"

# 向量数据库（通常无需修改）
DB_DIR = 'backend/src/data/chroma_db'
COLLECTION_NAME = "ffmpeg_docs"
```

### 4. 启动后端

```shell
python backend/app/main.py
```

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
- 上传的文件会显示在"已上传文件"列表中

> 每次上传新文件时，之前的文件会被自动清除。

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
- 输出文件也会出现在左侧"已完成文件"列表中，可随时下载

---

## API 一览

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/api/upload` | 上传文件（自动清除旧文件） |
| `POST` | `/api/chat` | 发送问题，返回 SSE 流式回复 |
| `GET` | `/api/upload` | 列出已上传的文件 |
| `GET` | `/api/upload/{filename}` | 下载已上传的文件 |
| `DELETE` | `/api/upload/{filename}` | 删除已上传的文件 |
| `GET` | `/api/output` | 列出已完成输出的文件 |
| `GET` | `/api/output/{filename}` | 下载输出文件 |

`/api/chat` 返回 [SSE (Server-Sent Events)](https://developer.mozilla.org/zh-CN/docs/Web/API/Server-sent_events) 流，事件类型：

| 事件 | 说明 |
|---|---|
| `meta` | 输出文件信息（如有） |
| `token` | AI 回复的文字片段 |
| `done` | 回复结束 |
| `error` | 发生错误 |

---

## 项目结构

```
ffmpeg-agent/
├── backend/
│   ├── app/
│   │   ├── main.py          # FastAPI 服务端 + API 路由
│   │   ├── agents.py        # 三个 AI Agent 的定义
│   │   ├── graph.py         # LangGraph 工作流编排
│   │   ├── tools.py         # Agent 工具（查知识库、执行命令等）
│   │   ├── model.py         # LLM 配置（智谱 API）
│   │   ├── db_search.py     # ChromaDB 向量检索
│   │   └── clear.py         # 清理工具
│   ├── src/data/
│   │   ├── chroma_db/       # ChromaDB 向量数据库
│   │   └── bge_small/       # BGE 嵌入模型
│   └── tests/
├── frontend/
│   ├── src/
│   │   ├── App.vue          # 主界面（文件管理 + 对话）
│   │   └── main.js          # Vue 入口
│   ├── vite.config.js       # Vite 配置（含 API 代理）
│   └── package.json
├── requirements.txt         # Python 依赖
├── docker-deploy-guide.md   # Docker 部署指南
└── .env                     # 环境变量配置
```

---

## 技术栈

**Frontend**：Vue 3 + Vite

**Backend**：Python FastAPI + Uvicorn

**AI & Data**：
- [LangChain](https://www.langchain.com/) — Agent 框架
- [LangGraph](https://langchain-ai.github.io/langgraph/) — 工作流编排（Search → Execute 循环）
- [ChromaDB](https://www.trychroma.com/) — 向量数据库（FFmpeg 知识库）
- [BGE Embeddings](https://github.com/FlagOpen/FlagEmbedding) — 中文嵌入模型
- ChatOpenAI（兼容接口）— 大语言模型

---

## Docker 部署

提供了完整的 Docker 部署指南，详见 [`docker-deploy-guide.md`](./docker-deploy-guide.md)。

一句话部署（需要先按指南创建 Dockerfile）：

```shell
docker build -t ffmpeg-agent .
docker run -d --name ffmpeg-agent -p 8080:8000 -e API_KEY="你的密钥" ffmpeg-agent
```

然后访问 `http://localhost:8080`

---

## 常见问题

**Q：支持哪些大模型？**
任何兼容 OpenAI API 的模型都可以，在 `.env` 中配置 `BASE_URL` 和 `API_KEY` 即可。例如智谱 AI、DeepSeek、通义千问等。

**Q：知识库里有什么内容？**
知识库存放的是 FFmpeg 官方文档的中文翻译片段。当你提问时，Agent 会先检索相关知识，再生成准确的命令。

**Q：执行过程安全吗？**
工具层做了严格限制：只能执行 `ffmpeg` 开头的命令，其他命令（`rm`、`sudo`、`python` 等）会被拒绝。

**Q：处理大文件会卡死吗？**
FFmpeg 命令是同步执行的，大文件处理期间服务器会阻塞。建议处理适当大小的文件。
