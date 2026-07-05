# ffmpeg-agent Docker 部署指南

> 本文档面向 Docker 初学者，每个步骤都附有命令说明。

---

## 准备工作

安装 Docker Desktop（Windows）：

1. 前往 https://www.docker.com/products/docker-desktop/
2. 下载并安装 Docker Desktop for Windows
3. 安装完成后启动 Docker Desktop，等待右下角图标变绿

验证安装：

```powershell
docker --version
```

---

## 文件清单

在项目根目录（`E:\code\ffmpeg-agent\`）下，你需要创建 2 个文件，修改 1 个文件：

| 文件                    | 操作           | 说明                                       |
| ----------------------- | -------------- | ------------------------------------------ |
| `.dockerignore`       | **新建** | 告诉 Docker 构建镜像时忽略哪些文件         |
| `Dockerfile`          | **新建** | 告诉 Docker 如何构建镜像                   |
| `backend/app/main.py` | **修改** | 添加一行代码，让后端服务器同时托管前端页面 |

---

## Step 1: 创建 `.dockerignore`

在项目根目录新建文件，命名为 `.dockerignore`（注意前面有个点）。

**文件路径：** `E:\code\ffmpeg-agent\.dockerignore`

**文件内容：**

```
__pycache__
.git
.gitignore
.venv
node_modules
backend/upload
backend/download
*.pyc
.DS_Store
```

**这个文件有什么用？**

就像 `.gitignore` 一样，`.dockerignore` 告诉 Docker 在构建镜像时忽略这些文件和目录，避免把缓存、密钥等不必要的东西打包进镜像，减少镜像体积。

---

## Step 2: 创建 `Dockerfile`

在项目根目录新建文件，命名为 `Dockerfile`（注意没有后缀名）。

**文件路径：** `E:\code\ffmpeg-agent\Dockerfile`

**文件内容：**

```dockerfile
FROM python:3.10-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

COPY . .

RUN mkdir -p backend/upload backend/download

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

**逐行解释：**

| 指令                              | 说明                                                         |
| --------------------------------- | ------------------------------------------------------------ |
| `FROM python:3.10-slim`         | 使用 Python 3.10 的精简版镜像作为基础                        |
| `RUN apt-get update ... ffmpeg` | 安装 ffmpeg 命令行工具（你的项目需要它处理音视频）           |
| `WORKDIR /app`                  | 设置容器内的工作目录为`/app`                               |
| `COPY requirements.txt .`       | 先把依赖文件复制进去（这一步单独写，是为了利用 Docker 缓存） |
| `RUN pip install ...`           | 安装 Python 依赖包                                           |
| `COPY . .`                      | 把项目所有文件复制到容器内                                   |
| `RUN mkdir -p ...`              | 创建上传和下载目录                                           |
| `EXPOSE 8000`                   | 声明容器会监听 8000 端口（仅作为文档说明）                   |
| `CMD [...]`                     | 容器启动时执行的命令：启动 uvicorn 服务器                    |

---

## Step 3: 修改 `backend/app/main.py`

用任意文本编辑器打开 `E:\code\ffmpeg-agent\backend\app\main.py`。

**找到文件末尾最后几行：**

```python
if __name__ == '__main__':
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
```

**在文件末尾（`if __name__` 之前），添加这两行：**

```python
from fastapi.staticfiles import StaticFiles
app.mount("/", StaticFiles(directory="frontend/dist", html=True), name="frontend")
```

**修改后的文件结尾应该是这样：**

```python
from fastapi.staticfiles import StaticFiles
app.mount("/", StaticFiles(directory="frontend/dist", html=True), name="frontend")

if __name__ == '__main__':
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
```

> **注意：** 必须把所有 API 路由（`@app.get`、`@app.post` 等）都写在 `app.mount` 的**前面**，否则 API 接口会被静态文件路由拦截而无法访问。

**这行代码的作用：**

告诉 FastAPI，除了 `/api/*` 这些 API 路由之外，其他所有请求都去 `frontend/dist` 目录下找对应的静态文件（HTML、JS、CSS）。`html=True` 表示如果找不到文件，就返回 `index.html`——这样刷新页面时不会 404。

---

## Step 4: 构建 Docker 镜像

打开 PowerShell（或命令提示符），进入项目根目录：

```powershell
cd E:\code\ffmpeg-agent
```

执行构建命令：

```powershell
docker build -t ffmpeg-agent .
```

| 参数                | 说明                                 |
| ------------------- | ------------------------------------ |
| `build`           | 构建镜像                             |
| `-t ffmpeg-agent` | 给镜像打上标签（名字），方便后续引用 |
| `.`               | 使用当前目录下的 Dockerfile 来构建   |

> **说明：** Dockerfile 中 apt 和 pip 已配置清华镜像源，国内可直接访问，无需 `--build-arg` 代理。`docker pull` 拉取基础镜像如果慢，需配置 Docker 守护进程代理（见常见问题）。

**首次构建需要多久？**

- 下载基础镜像 `python:3.10-slim` ~ 几分钟
- 安装 Python 依赖 ~ 1-2 分钟
- 复制项目文件 ~ 十几秒（因为包含了 BGE 模型 ~200MB）
- 总共约 **5-10 分钟**，取决于你的网络速度

**构建成功后你会看到类似输出：**

```
Successfully built a1b2c3d4e5f6
Successfully tagged ffmpeg-agent:latest
```

---

## Step 5: 运行容器

```powershell
docker run -d `
  --name ffmpeg-agent `
  -p 8080:8000 `
  -e API_KEY="你的智谱AI密钥" `
  ffmpeg-agent
```

**逐参数解释：**

| 参数                    | 说明                                                                  |
| ----------------------- | --------------------------------------------------------------------- |
| `run`                 | 运行容器                                                              |
| `-d`                  | 后台运行（detached），关掉终端也不会停止                              |
| `--name ffmpeg-agent` | 给容器起个名字，方便后续管理                                          |
| `-p 8080:8000`        | 端口映射：访问本机 8080 端口 = 访问容器内的 8000 端口                 |
| `-e API_KEY="..."`    | 设置环境变量**覆盖** `.env` 中的值（避免 API 密钥写在镜像里） |
| `ffmpeg-agent`        | 使用哪个镜像来创建容器                                                |

**运行成功后你会看到：**

```
7986a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9
```

这一长串是容器的唯一 ID。

---

## Step 6: 验证部署

打开浏览器，访问：

```
http://localhost:8080
```

你应该能看到 ffmpeg-agent 的前端页面。

**测试 API 是否正常：**

```powershell
# 列出上传文件（应该返回空列表）
curl http://localhost:8080/api/upload
```

返回结果：`{"files":[]}`

**测试 AI 对话（替换为你的问题）：**

```powershell
curl -X POST http://localhost:8080/api/chat -d "question=你好"
```

---

## 常用命令速查

```powershell
# 查看运行中的容器
docker ps

# 查看所有容器（包括已停止的）
docker ps -a

# 查看容器日志（持续跟踪加 -f）
docker logs ffmpeg-agent
docker logs -f ffmpeg-agent

# 停止容器
docker stop ffmpeg-agent

# 启动已停止的容器
docker start ffmpeg-agent

# 删除容器（需要先停止）
docker rm ffmpeg-agent

# 删除镜像
docker rmi ffmpeg-agent

# 进入容器内部（调试用）
docker exec -it ffmpeg-agent bash

# 查看镜像列表
docker images
```

---

## 升级镜像

当你修改了代码后，需要重新构建和运行：

```powershell
# 1. 停止并删除旧容器
docker stop ffmpeg-agent
docker rm ffmpeg-agent

# 2. 重新构建镜像（利用缓存会很快）
docker build -t ffmpeg-agent .

# 3. 重新运行
docker run -d `
  --name ffmpeg-agent `
  -p 8080:8000 `
  -e API_KEY="你的智谱AI密钥" `
  ffmpeg-agent
```

---

## 常见问题

### Q: 构建时报错 "pip install failed"

可能是网络问题。在 `Dockerfile` 的 `pip install` 行可以换成国内镜像源（已经在上面给出，使用清华源）：

```
RUN pip install --no-cache-dir -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

或者启用 Docker 守护进程代理（见下一条）。

### Q: 拉取基础镜像（`FROM python:...`）很慢 / 超时

`docker pull`（拉取基础镜像）走的是 Docker 守护进程网络，**不受 Dockerfile 内的设置影响**。需要给 Docker 守护进程配置代理：

1. 打开 Docker Desktop → Settings → Resources → Proxies
2. 填写：
   - HTTP proxy: `http://127.0.0.1:7897`
   - HTTPS proxy: `http://127.0.0.1:7897`
   - No proxy: `localhost,127.0.0.1,::1`
3. Apply & Restart

之后 `docker build` 拉取基础镜像也会走代理。

如果不想改全局设置，也可以只对这次构建临时生效（**管理员 PowerShell**）：

```powershell
$env:HTTP_PROXY="http://127.0.0.1:7897"
$env:HTTPS_PROXY="http://127.0.0.1:7897"
docker build -t ffmpeg-agent .
```

> 注意：环境变量方式不一定对所有 Docker 版本生效，最保险还是 Docker Desktop 的设置。

### Q: 运行后浏览器无法访问

1. 检查容器是否在运行：`docker ps`，看 `ffmpeg-agent` 是否在列表中
2. 查看日志找错误：`docker logs ffmpeg-agent`
3. 确认端口映射正确：`docker ps` 应该看到 `0.0.0.0:8080 -> 8000/tcp`
4. 确保 Docker Desktop 已经启动（右下角图标是绿色的）

### Q: 容器内需要联网访问智谱AI API

默认容器可以访问外网，只要你的宿主机能联网就行。如果使用了公司代理，需要在运行容器时添加代理环境变量：

```powershell
docker run -d `
  --name ffmpeg-agent `
  -p 8080:8000 `
  -e API_KEY="你的智谱AI密钥" `
  -e HTTP_PROXY="http://代理地址:端口" `
  -e HTTPS_PROXY="http://代理地址:端口" `
  ffmpeg-agent
```

### Q: 容器重启后上传的文件丢失了

容器的文件系统是临时的，容器删除后数据会丢失。如果想持久化保存上传/下载的文件，在运行时添加卷映射：

```powershell
docker run -d `
  --name ffmpeg-agent `
  -p 8080:8000 `
  -e API_KEY="你的智谱AI密钥" `
  -v E:\code\ffmpeg-agent\backend\upload:/app/backend/upload `
  -v E:\code\ffmpeg-agent\backend\download:/app/backend/download `
  ffmpeg-agent
```

`-v` 参数把宿主机的目录映射到容器内，这样容器删除后数据还在。

### Q: 镜像体积太大（~1GB+）

这是正常的，因为：

- 基础镜像 `python:3.10-slim` ~ 120MB
- Python 依赖包 ~ 数百 MB
- BGE 嵌入模型 ~ 200MB
- ffmpeg 及其依赖 ~ 数十 MB

可以后续使用多阶段构建（multi-stage build）来优化，但初学者可以先忽略。

---

## Clash 代理规则配置

要让 Docker 正确走 Clash 代理，需要在 Clash 中添加规则或使用代理模式。

### 方法 1：使用全局代理（最简单）

在 Clash 面板中切到 **Global**（全局）模式，所有流量都走代理。构建完切回 Rule 模式即可。

### 方法 2：配置规则（推荐）

Clash 的规则文件通常位于 Clash 安装目录下的 `config.yaml`（或 Clash Verge / Clash Meta 等客户端的订阅配置中）。添加以下规则让 Docker 相关域名走代理：

```yaml
# Docker 镜像拉取走代理
- DOMAIN-SUFFIX,docker.io,PROXY
- DOMAIN-SUFFIX,registry-1.docker.io,PROXY
- DOMAIN-SUFFIX,production.cloudflare.docker.com,PROXY
- DOMAIN-SUFFIX,pypi.org,PROXY
- DOMAIN-SUFFIX,storage.googleapis.com,PROXY
- DOMAIN-SUFFIX,github.com,PROXY
- DOMAIN-SUFFIX,githubusercontent.com,PROXY
```

如果你使用 **Clash Verge** 或 **Clash Nyanpasu** 等图形客户端：

1. 打开客户端界面 → **设置 / Settings** → **规则 / Rules**
2. 点击编辑 / 添加规则，粘贴上面的内容
3. 保存并重载配置

如果使用 **Mihomo Party / Clash Meta**：

1. 打开 Dashboard → 配置 → 编辑配置文件
2. 在 `rules:` 字段下添加上述规则
3. 保存并重载

> **技巧：** 你不需要添加所有域名，只要把模式设为 **Rule**，然后在 Clash 日志中观察 `docker build` 时访问了哪些域名，逐个添加即可。或者直接用 **Global 模式** 省事。

### 验证代理是否生效

查看容器内 IP 是否走了代理出口：

```powershell
# 构建成功后，查看容器网络
docker run --rm -it ffmpeg-agent curl -s ifconfig.me
```

如果返回的 IP 不是你本地的，说明代理生效。

---

## 完整流程总结

```
1. 创建 .dockerignore
2. 创建 Dockerfile
3. 修改 main.py（添加 StaticFiles 挂载）
4. docker build -t ffmpeg-agent .         ← 构建镜像
5. docker run -d --name ffmpeg-agent      ← 运行容器
     -p 8080:8000
     -e API_KEY="your-key"
     ffmpeg-agent
6. 打开 http://localhost:8080              ← 开始使用！
```
