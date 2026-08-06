import os, sys, shutil, json, atexit, io, zipfile, datetime, logging, asyncio, threading, traceback

# ── 冻结模式（PyInstaller 打包）预处理 ──
# 必须在任何重依赖 import 之前执行：
#   1) chdir 到 exe 所在目录，保证相对路径（backend/upload、frontend/dist 等）正确
#   2) 无控制台窗口，把 stdout/stderr 重定向到 backend/logs/app.log
#   3) 提前设置数据目录环境变量（db_search/tools 在 import 时读取，需提前注入）
FROZEN = bool(getattr(sys, 'frozen', False))
if FROZEN:
    _exe_dir = os.path.dirname(os.path.abspath(sys.executable))
    os.chdir(_exe_dir)
    _log_dir = os.path.join(_exe_dir, 'backend', 'logs')
    os.makedirs(_log_dir, exist_ok=True)
    _log_path = os.path.join(_log_dir, 'app.log')
    sys.stdout = open(_log_path, 'a', encoding='utf-8', buffering=1)
    sys.stderr = open(_log_path, 'a', encoding='utf-8', buffering=1)
    os.environ.setdefault('DB_DIR', os.path.join(_exe_dir, 'backend', 'data', 'chroma_db'))
    os.environ.setdefault('COLLECTION_NAME', 'ffmpeg_docs')
    os.environ.setdefault('PROBE_COLLECTION_NAME', 'ffprobe_docs')
    os.environ.setdefault('BGE_CACHE_DIR', os.path.join(_exe_dir, 'backend', 'data', 'bge_onnx'))
    os.environ.setdefault('UPLOAD', os.path.join(_exe_dir, 'backend', 'upload'))
    os.environ.setdefault('DOWNLOAD', os.path.join(_exe_dir, 'backend', 'download'))

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

from dotenv import load_dotenv
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse, Response
from fastapi.middleware.cors import CORSMiddleware

logger = logging.getLogger(__name__)

# dev 模式：确保 CWD 指向项目根目录，使后续 import 和 load_dotenv() 路径正确
if not FROZEN:
    _backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    os.chdir(os.path.dirname(_backend_dir))
    if _backend_dir not in sys.path:
        sys.path.insert(0, _backend_dir)

# 重依赖（langchain → transformers/torch）导入可能耗时数十秒，先打印提示避免"长时间无输出"
print('正在启动 FFmpeg Agent，加载后端依赖（首次约需 10~60 秒）...', flush=True)


def _step_import(label, what):
    print(f'[step] {label} ({what}) ...', flush=True)
    import importlib
    module = importlib.import_module(what)
    print(f'[step] {label} ok', flush=True)
    return module


_graph_mod = _step_import('app.graph', 'app.graph')
exec_graph = _graph_mod.exec_graph
build_chat_prompt = _graph_mod.build_chat_prompt
probe_exec_graph = _graph_mod.probe_exec_graph
build_probe_chat_prompt = _graph_mod.build_probe_chat_prompt

_agents_mod = _step_import('app.agents', 'app.agents')
ensure_agents = _agents_mod.ensure_agents
ensure_probe_agents = _agents_mod.ensure_probe_agents

_messages_mod = _step_import('langchain.messages', 'langchain.messages')
HumanMessage = _messages_mod.HumanMessage

print('后端依赖加载完成', flush=True)

load_dotenv()

UPLOAD_DIR = os.getenv('UPLOAD', 'backend/upload')
DOWNLOAD_DIR = os.getenv('DOWNLOAD', 'backend/download')

# 冻结模式下资源在 _MEIPASS（onedir = _internal 目录）内
FRONTEND_DIST = os.path.join(sys._MEIPASS, 'frontend', 'dist') if FROZEN else 'frontend/dist'

# 启动时清理历史数据 + 程序退出时清理
def _cleanup():
    for _dir in (UPLOAD_DIR, DOWNLOAD_DIR):
        if os.path.exists(_dir):
            shutil.rmtree(_dir)
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)

_cleanup()
atexit.register(_cleanup)

app = FastAPI(title="ffmpeg-agent")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# 初始化状态：冻结模式下预加载在后台线程执行，健康检查据此返回状态
_init_state = {'status': 'running', 'progress': 0, 'step': '启动中', 'error': None}  # running / ok / error


def _preload():
    try:
        def _progress(pct, msg):
            _init_state['progress'] = pct
            _init_state['step'] = msg
            logger.info(f'预加载进度 {pct}%：{msg}')

        if FROZEN:
            from app.ffmpeg_download import ensure_ffmpeg_bin
            _bin_dir = os.path.join(os.path.dirname(os.path.abspath(sys.executable)), 'backend', 'bin')
            _progress(0, '检查 ffmpeg...')
            ensure_ffmpeg_bin(_bin_dir, on_progress=_progress)

            # 使用内置（打包期构建）向量库，避免首跑联网抓取 ffmpeg.org
            _bundled_db = os.path.join(sys._MEIPASS, 'backend', 'data', 'chroma_db')
            _db_dir = os.environ.get('DB_DIR', '')
            if (_db_dir and not os.path.isfile(os.path.join(_db_dir, 'chroma.sqlite3'))
                    and os.path.isfile(os.path.join(_bundled_db, 'chroma.sqlite3'))):
                os.makedirs(_db_dir, exist_ok=True)
                shutil.copytree(_bundled_db, _db_dir, dirs_exist_ok=True)
                _progress(70, '准备内置知识库...')

        _progress(80, '初始化 ffmpeg 知识库...')
        from app.db_search import _ensure_vector_db, _get_vector_db, _ensure_probe_vector_db, _get_probe_vector_db
        _ensure_vector_db()
        _get_vector_db()
        _progress(95, '初始化 ffprobe 知识库...')
        _ensure_probe_vector_db()
        _get_probe_vector_db()
        _progress(100, '就绪')
        _init_state['status'] = 'ok'
        logger.info('预加载完成')
    except Exception:
        logger.exception('预加载模型或向量库失败')
        _init_state['status'] = 'error'
        _init_state['error'] = traceback.format_exc()


@app.on_event("startup")
def preload_models():
    # 冻结模式下后台预加载，不阻塞 web 服务（首跑建库需数分钟）
    if FROZEN:
        threading.Thread(target=_preload, daemon=True).start()
    else:
        _preload()


def _clear_dir(path: str):
    if os.path.exists(path):
        shutil.rmtree(path)
    os.makedirs(path, exist_ok=True)


def _save_with_timestamp(file: UploadFile, seq: int) -> str:
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    stem, ext = os.path.splitext(file.filename or "file")
    name = f"{stem}_{stamp}_{seq}{ext}"
    with open(os.path.join(UPLOAD_DIR, name), 'wb') as f:
        f.write(file.file.read())
    return name


@app.post("/api/upload")
async def upload_files(files: list[UploadFile] = File(...)):
    """上传一个或多个文件到 upload/，不会删除旧文件"""
    logger.info('上传文件')
    logger.info(f'文件数量：{len(files)}')
    saved = []
    counter = {}
    for f in files:
        name = f.filename or "file"
        counter[name] = counter.get(name, 0) + 1
        saved.append(_save_with_timestamp(f, counter[name]))
    logger.info(f'保存文件：{saved}')
    return {"uploaded": saved}


async def _event_stream(question: str, graph_fn, chat_agent, prompt_builder):
    """公共 SSE 流：graph 进度 → meta → chat 逐 token → done"""
    progress = []
    graph_task = asyncio.create_task(
        asyncio.to_thread(graph_fn, question, progress)
    )

    while not graph_task.done():
        while progress:
            yield f"data: {json.dumps({'event': 'status', 'text': progress.pop(0)})}\n\n"
        await asyncio.sleep(0.2)

    try:
        exec_state = await graph_task
    except Exception as e:
        logger.error(f'图谱执行失败：{e}')
        yield f"data: {json.dumps({'event': 'error', 'text': f'知识库查询或命令执行失败：{str(e)}'})}\n\n"
        yield "data: {\"event\": \"done\"}\n\n"
        return

    output_file = exec_state.get('output_file', '') or ''

    yield f"data: {json.dumps({'event': 'meta', 'output_file': output_file})}\n\n"

    yield f"data: {json.dumps({'event': 'status', 'text': '正在生成回答...'})}\n\n"

    chat_prompt = prompt_builder(exec_state)
    full_text = ''
    try:
        async for event in chat_agent.astream_events(
            {"messages": [HumanMessage(content=chat_prompt)]},
            version="v2",
        ):
            if event["event"] == "on_chat_model_stream":
                chunk = event["data"]["chunk"]
                content = getattr(chunk, 'content', '')
                if content:
                    full_text += content
                    yield f"data: {json.dumps({'event': 'token', 'text': content})}\n\n"
    except Exception as e:
        yield f"data: {json.dumps({'event': 'error', 'text': str(e)})}\n\n"
        return

    yield "data: {\"event\": \"done\"}\n\n"

    if full_text:
        logger.info(f'AI回复：{full_text[:200]}')
    if output_file:
        logger.info(f'输出文件：{output_file}')


def _sanitize_selected_files(files: list[str]) -> list[str]:
    """清洗选中的文件：支持 'upload:xxx' / 'download:xxx' 前缀（裸文件名默认 upload）。
    仅保留对应目录中实际存在的 basename（防路径穿越），返回项目根目录下的相对路径。"""
    result = []
    for entry in files or []:
        entry = (entry or '').strip()
        src, _, name = entry.partition(':')
        if src not in ('upload', 'download'):
            src, name = 'upload', entry
        name = os.path.basename(name)
        base = DOWNLOAD_DIR if src == 'download' else UPLOAD_DIR
        path = os.path.join(base, name)
        if name and os.path.isfile(path) and path not in result:
            result.append(path)
    return result


@app.post("/api/chat")
async def chat(question: str = Form(...), files: list[str] = Form(default=[])):
    """发送问题 → 流式输出（ffmpeg search+execute 进度 + chat 逐 token）"""
    if _init_state['status'] == 'running':
        return Response(
            content=f"data: {json.dumps({'event': 'error', 'text': '正在初始化知识库（首次运行需下载模型，请稍候）'})}\n\ndata: {json.dumps({'event': 'done'})}\n\n",
            media_type="text/event-stream",
        )

    if not ensure_agents():
        return Response(
            content=f"data: {json.dumps({'event': 'error', 'text': 'LLM 未配置，请先在页面右上角 ⚙️ 设置中填写模型信息'})}\n\ndata: {json.dumps({'event': 'done'})}\n\n",
            media_type="text/event-stream",
        )

    selected = _sanitize_selected_files(files)
    if not selected:
        return Response(
            content=f"data: {json.dumps({'event': 'error', 'text': '请先选择要处理的文件，再发起需求'})}\n\ndata: {json.dumps({'event': 'done'})}\n\n",
            media_type="text/event-stream",
        )

    logger.info('处理对话')
    logger.info(f'用户问题：{question[:200]}')
    logger.info(f'选择文件：{selected}')

    from app.agents import agent_chat

    return StreamingResponse(
        _event_stream(question, lambda q, p: exec_graph(q, p, files=selected), agent_chat, build_chat_prompt),
        media_type="text/event-stream",
    )


@app.post("/api/probe/chat")
async def probe_chat(question: str = Form(...), files: list[str] = Form(default=[])):
    """发送问题 → 流式输出（ffprobe search+execute 进度 + chat 逐 token）"""
    if _init_state['status'] == 'running':
        return Response(
            content=f"data: {json.dumps({'event': 'error', 'text': '正在初始化知识库（首次运行需下载模型，请稍候）'})}\n\ndata: {json.dumps({'event': 'done'})}\n\n",
            media_type="text/event-stream",
        )

    if not ensure_probe_agents():
        return Response(
            content=f"data: {json.dumps({'event': 'error', 'text': 'LLM 未配置，请先在页面右上角 ⚙️ 设置中填写模型信息'})}\n\ndata: {json.dumps({'event': 'done'})}\n\n",
            media_type="text/event-stream",
        )

    selected = _sanitize_selected_files(files)
    if not selected:
        return Response(
            content=f"data: {json.dumps({'event': 'error', 'text': '请先选择要处理的文件，再发起需求'})}\n\ndata: {json.dumps({'event': 'done'})}\n\n",
            media_type="text/event-stream",
        )

    logger.info('处理 ffprobe 对话')
    logger.info(f'用户问题：{question[:200]}')
    logger.info(f'选择文件：{selected}')

    from app.agents import agent_probe_chat

    return StreamingResponse(
        _event_stream(question, lambda q, p: probe_exec_graph(q, p, files=selected), agent_probe_chat, build_probe_chat_prompt),
        media_type="text/event-stream",
    )


@app.get("/api/output")
async def list_output():
    """列出 download/ 中的已完成文件"""
    logger.info('列出已完成文件')
    if not os.path.exists(DOWNLOAD_DIR):
        return {"files": []}
    files = [f for f in os.listdir(DOWNLOAD_DIR) if os.path.isfile(os.path.join(DOWNLOAD_DIR, f))]
    logger.info(f'文件列表：{files}')
    return {"files": files}


@app.delete("/api/output/{filename:path}")
async def delete_output(filename: str):
    """删除 download/ 中的已完成文件"""
    logger.info('删除已完成文件')
    logger.info(f'文件名：{filename}')
    path = os.path.join(DOWNLOAD_DIR, filename)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="文件不存在")
    os.remove(path)
    return {"deleted": filename}


@app.post("/api/output/delete")
async def batch_delete_output(body: dict):
    """批量删除输出文件：POST {"files": ["a.mp4", "b.jpg"]}"""
    files = body.get("files", [])
    results = {"deleted": [], "not_found": []}
    for f in files:
        path = os.path.join(DOWNLOAD_DIR, f)
        if os.path.exists(path):
            os.remove(path)
            results["deleted"].append(f)
        else:
            results["not_found"].append(f)
    return results


@app.post("/api/output/download")
async def batch_download_output(body: dict):
    """批量下载输出文件为 ZIP：POST {"files": ["a.mp4", "b.jpg"]}"""
    files = body.get("files", [])
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for f in files:
            path = os.path.join(DOWNLOAD_DIR, f)
            if os.path.exists(path):
                zf.write(path, arcname=f)
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=outputs.zip"},
    )


@app.get("/api/output/{filename:path}")
async def get_output(filename: str):
    """返回 download/ 中的文件"""
    logger.info('下载已完成文件')
    logger.info(f'文件名：{filename}')
    path = os.path.join(DOWNLOAD_DIR, filename)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="文件不存在")
    return FileResponse(path)


@app.get("/api/upload")
async def list_uploaded():
    """列出 upload/ 中的文件"""
    logger.info('列出上传文件')
    if not os.path.exists(UPLOAD_DIR):
        return {"files": []}
    files = [f for f in os.listdir(UPLOAD_DIR) if os.path.isfile(os.path.join(UPLOAD_DIR, f))]
    logger.info(f'文件列表：{files}')
    return {"files": files}


@app.get("/api/upload/{filename:path}")
async def get_uploaded(filename: str):
    """返回 upload/ 中的文件供下载"""
    logger.info('下载上传文件')
    logger.info(f'文件名：{filename}')
    path = os.path.join(UPLOAD_DIR, filename)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="文件不存在")
    return FileResponse(path)


@app.delete("/api/upload/{filename:path}")
async def delete_uploaded(filename: str):
    """删除 upload/ 中的文件"""
    logger.info('删除上传文件')
    logger.info(f'文件名：{filename}')
    path = os.path.join(UPLOAD_DIR, filename)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="文件不存在")
    os.remove(path)
    logger.info(f'删除成功：{filename}')
    return {"deleted": filename}

from fastapi.staticfiles import StaticFiles


# ── LLM 设置 ──
from app.model import get_model_config, update_model_config

_settings_store = dict(get_model_config())


@app.get("/api/settings/llm")
async def get_llm_settings():
    settings = get_model_config()
    _settings_store.update(settings)
    return _settings_store


@app.put("/api/settings/llm")
async def update_llm_settings(body: dict):
    update_model_config(body)
    cfg = get_model_config()
    _settings_store.update(cfg)
    return _settings_store


@app.get("/api/health")
async def health():
    return {
        "status": _init_state['status'],
        "progress": _init_state.get('progress', 0),
        "step": _init_state.get('step', ''),
        "error": _init_state.get('error', ''),
    }


app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")

if __name__ == '__main__':
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000)
