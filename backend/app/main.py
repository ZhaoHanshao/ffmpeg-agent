import os, sys, shutil, json, re, io, zipfile, datetime, logging, asyncio, threading, time, traceback, secrets
from contextlib import asynccontextmanager

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
get_chat_agent = _graph_mod.get_chat_agent
GraphCancelled = _graph_mod.GraphCancelled

_agents_mod = _step_import('app.agents', 'app.agents')
# 现在是按对话级配置取 agent（GraphSpec.ensure / agents_for），
# 不再直接持有 ensure_agents，避免"全局一份 agent"的旧假设又被引回来。

_messages_mod = _step_import('langchain.messages', 'langchain.messages')
HumanMessage = _messages_mod.HumanMessage

print('后端依赖加载完成', flush=True)

load_dotenv()

UPLOAD_DIR = os.getenv('UPLOAD', 'backend/upload')
DOWNLOAD_DIR = os.getenv('DOWNLOAD', 'backend/download')

# 上传限制：单文件最大体积(MB,0=不限制)与可选扩展名白名单(逗号分隔,空=不限制)
try:
    MAX_UPLOAD_SIZE = int(os.getenv('MAX_UPLOAD_SIZE_MB', '2048') or 0) * 1024 * 1024
except ValueError:
    MAX_UPLOAD_SIZE = 0
UPLOAD_EXT_WHITELIST = {e.lower().lstrip('.') for e in os.getenv('UPLOAD_EXT_WHITELIST', '').split(',') if e.strip()}

# 冻结模式下资源在 _MEIPASS（onedir = _internal 目录）内
FRONTEND_DIST = os.path.join(sys._MEIPASS, 'frontend', 'dist') if FROZEN else 'frontend/dist'

# 数据目录仅确保存在,不再启动/退出时清空:上传文件与转码成果跨重启保留
def _ensure_dirs():
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)

_ensure_dirs()

app = FastAPI(title="ffmpeg-agent")

# 安全配置：设置 AUTH_TOKEN 后所有 /api/* 接口(除 /api/health)都需要携带令牌
AUTH_TOKEN = os.getenv('AUTH_TOKEN', '').strip()
# CORS：仅当显式配置 CORS_ORIGINS(逗号分隔)时才允许跨域,默认同源(前端由后端托管或走 dev 代理)
CORS_ORIGINS = [o.strip() for o in os.getenv('CORS_ORIGINS', '').split(',') if o.strip()]

if CORS_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=CORS_ORIGINS,
        allow_methods=["*"],
        allow_headers=["*"],
    )


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    if AUTH_TOKEN and request.url.path.startswith('/api/') and request.url.path != '/api/health':
        token = request.headers.get('X-Auth-Token') or ''
        auth = request.headers.get('Authorization') or ''
        if auth.startswith('Bearer '):
            token = auth[7:]
        if not (token and secrets.compare_digest(token, AUTH_TOKEN)):
            return Response(
                content=json.dumps({'detail': '未授权：缺少或错误的访问令牌'}),
                status_code=401,
                media_type='application/json',
            )
    return await call_next(request)


# 初始化状态：冻结模式下预加载在后台线程执行，健康检查据此返回状态
_init_state = {'status': 'running', 'progress': 0, 'step': '启动中', 'error': None}  # running / ok / error

# 运行中的聊天任务:job_id → {'stop', 'proc', 'owner', 'created_at', 'kind'},用于真实取消
_jobs = {}
_jobs_lock = threading.Lock()

# 任务记录的最长保留时间(秒)。正常路径会在流结束时注销；
# 这里兜底清理"客户端在流开始消费前就断开"等导致注册后无人注销的记录，
# 避免 _jobs 随请求数无限增长。
JOB_TTL_SECONDS = int(os.getenv('JOB_TTL_SECONDS', '7200') or 7200)


def _register_job(kind: str, owner: str = ''):
    job_id = os.urandom(6).hex()
    now = time.time()
    job = {
        'stop': threading.Event(),
        'proc': [None],
        # 归属标识：/api/chat/stop 需要校验，避免任意客户端凭 job_id 终止他人任务
        'owner': owner or job_id,
        'created_at': now,
        'kind': kind,
    }
    with _jobs_lock:
        # 顺手清理过期记录（无需额外线程）
        expired = [k for k, v in _jobs.items()
                   if now - v.get('created_at', now) > JOB_TTL_SECONDS]
        for k in expired:
            _jobs.pop(k, None)
        if expired:
            logger.info(f'清理 {len(expired)} 个过期任务记录')
        _jobs[job_id] = job
    return job_id, job


def _unregister_job(job_id: str):
    with _jobs_lock:
        _jobs.pop(job_id, None)


def _stop_job(job_id: str, owner: str = '') -> bool:
    with _jobs_lock:
        job = _jobs.get(job_id)
    if not job:
        return False
    # 归属校验：owner 不匹配时拒绝（job_id 本身即服务端签发的凭证，
    # 发起方始终持有它；伪造/猜测的 id 无法通过）
    if owner and job.get('owner') and not secrets.compare_digest(str(job['owner']), str(owner)):
        logger.warning(f'拒绝停止任务 {job_id}：归属不匹配')
        return False
    job['stop'].set()
    proc = job['proc'][0]
    if proc is not None and proc.poll() is None:
        try:
            proc.kill()
        except OSError:
            pass
    return True


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


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # 冻结模式下后台预加载，不阻塞 web 服务（首跑建库需数分钟）
    if FROZEN:
        threading.Thread(target=_preload, daemon=True).start()
    else:
        _preload()
    yield


app.router.lifespan_context = lifespan


def _safe_path(base: str, name: str):
    """将 name 约束到 base 目录内(防路径穿越)。

    返回规范化后的绝对路径;若 name 为空、含 NUL、是绝对路径或解析后逃出 base,返回 None。
    """
    if not name or '\x00' in name:
        return None
    base = os.path.realpath(os.path.abspath(base))
    candidate = os.path.abspath(os.path.join(base, os.path.normpath(name.lstrip('/\\'))))
    real = os.path.realpath(candidate)
    if real != base and not real.startswith(base + os.sep):
        return None
    return candidate


def _save_with_timestamp(file: UploadFile, seq: int) -> str:
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    raw = (file.filename or "file").replace('\\', '/')
    base = os.path.basename(raw) or 'file'
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    stem, ext = os.path.splitext(base)
    # 客户端可控的文件名必须清洗:只保留安全字符,杜绝 ../../ 与非法路径写入
    stem = re.sub(r'[^\w\-.]', '_', stem) or 'file'
    ext = re.sub(r'[^\w.]', '', ext)[:16]
    name = f"{stem}_{stamp}_{seq}{ext}"
    path = os.path.join(UPLOAD_DIR, name)
    total = 0
    try:
        with open(path, 'wb') as out:
            while True:
                chunk = file.file.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if MAX_UPLOAD_SIZE and total > MAX_UPLOAD_SIZE:
                    raise HTTPException(
                        status_code=413,
                        detail=f'文件超过大小限制({MAX_UPLOAD_SIZE // 1024 // 1024} MB)',
                    )
                out.write(chunk)
    except HTTPException:
        if os.path.isfile(path):
            os.remove(path)
        raise
    return name


@app.post("/api/upload")
async def upload_files(files: list[UploadFile] = File(...)):
    """上传一个或多个文件到 upload/，不会删除旧文件。

    整批上传按"先全量校验、再落盘、失败回滚"处理：任何一个文件不合法或
    落盘失败时，把本批已写入的文件删掉，避免留下半批"幽灵文件"
    （此前中途 415/写入失败会让前面的文件残留在 upload/ 且前端收不到列表）。
    """
    logger.info('上传文件')
    logger.info(f'文件数量：{len(files)}')

    # 阶段一：全量校验，避免写到一半才发现某个文件不合法
    if UPLOAD_EXT_WHITELIST:
        for f in files:
            name = f.filename or "file"
            ext = os.path.splitext(name)[1].lower().lstrip('.')
            if not ext or ext not in UPLOAD_EXT_WHITELIST:
                raise HTTPException(status_code=415, detail=f'不支持的文件类型：{ext or "(无扩展名)"}')

    # 阶段二：逐个落盘，任一失败则回滚本批已写入的文件
    saved = []
    counter = {}
    try:
        for f in files:
            name = f.filename or "file"
            counter[name] = counter.get(name, 0) + 1
            saved.append(_save_with_timestamp(f, counter[name]))
    except Exception:
        for name in saved:
            path = os.path.join(UPLOAD_DIR, name)
            try:
                if os.path.isfile(path):
                    os.remove(path)
            except OSError as e:
                logger.warning(f'回滚上传文件失败：{path}（{e}）')
        logger.warning(f'上传失败，已回滚 {len(saved)} 个文件')
        raise

    logger.info(f'保存文件：{saved}')
    return {"uploaded": saved}


def _friendly_error(e: Exception) -> str:
    """把 LLM 异常翻译成用户能照着做的提示。

    原先统一包成"知识库查询或命令执行失败：<原始异常>"，把真正原因（密钥失效、
    余额不足、模型名不存在）埋在一大段英文里，用户看到 401 也不知道该改什么。
    """
    status = getattr(e, 'status_code', None)
    if status == 401:
        return ('LLM 鉴权失败（401）：API Key 无效或已过期。'
                '请点击右上角 ⚙️ 打开设置，重新填写 API Key 后保存。')
    if status == 403:
        return 'LLM 拒绝访问（403）：当前 API Key 无权使用该模型，请检查模型名称或账号权限。'
    if status == 404:
        return 'LLM 接口返回 404：接口地址或模型名称不正确，请在 ⚙️ 设置中核对。'
    if status == 429:
        return 'LLM 请求过于频繁或额度不足（429）：请稍后重试，或检查账号余额与限速。'
    if isinstance(status, int) and status >= 500:
        return f'LLM 服务端错误（{status}）：这是模型服务商侧的问题，请稍后重试。'
    text = str(e)
    if 'Connection' in type(e).__name__ or 'Timeout' in type(e).__name__ or 'timed out' in text.lower():
        return f'无法连接 LLM 服务：请检查接口地址（BASE_URL）与网络。原始信息：{text[:200]}'
    return f'知识库查询或命令执行失败：{text[:400]}'


async def _event_stream(question: str, graph_fn, chat_agent, prompt_builder, job=None, prep_fn=None,
                        conversation_id: str = '', user_files: list = None):
    """公共 SSE 流：job → (素材理解) → graph 进度 → meta → chat 逐 token → done

    prep_fn(status_list) 可选：在图谱之前运行的准备步骤（多模态素材分析），
    返回值会注入图谱状态，供执行 agent 决定参数。

    conversation_id 非空时，本轮问答在流结束时落库（无论成功、失败还是被取消——
    失败同样是有信息量的历史，丢掉会让用户重开对话后不知道自己问过什么）。
    """
    stop_event = job['stop'] if job else None
    job_id = job['job_id'] if job else ''
    progress = []
    graph_task = None
    # 落库用的累积变量：必须在 try 之前初始化，提前 return 的路径也能拿到
    full_text = ''
    reply_error = ''
    output_file = ''
    try:
        yield f"data: {json.dumps({'event': 'job', 'job_id': job_id})}\n\n"

        # 多模态素材理解：先看图/波形再写参数。放在图谱之前，
        # 并把进展作为 status 推给前端（这一段会调用模型，耗时 1~5 秒）。
        media_analysis = ''
        if prep_fn is not None:
            prep_status = []
            yield f"data: {json.dumps({'event': 'status', 'text': '正在理解素材画面...'})}\n\n"
            prep_task = asyncio.create_task(asyncio.to_thread(prep_fn, prep_status))
            while not prep_task.done():
                while prep_status:
                    yield f"data: {json.dumps({'event': 'status', 'text': prep_status.pop(0)})}\n\n"
                await asyncio.sleep(0.2)
            try:
                media_analysis = await prep_task or ''
            except Exception as e:  # noqa: BLE001 - 分析失败不应阻断问答
                logger.warning(f'素材分析失败（继续执行）：{e}')
            while prep_status:
                yield f"data: {json.dumps({'event': 'status', 'text': prep_status.pop(0)})}\n\n"

        graph_task = asyncio.create_task(
            asyncio.to_thread(graph_fn, question, progress)
        )

        while not graph_task.done():
            while progress:
                yield f"data: {json.dumps({'event': 'status', 'text': progress.pop(0)})}\n\n"
            await asyncio.sleep(0.2)

        try:
            exec_state = await graph_task
        except GraphCancelled:
            yield f"data: {json.dumps({'event': 'cancelled'})}\n\n"
            yield "data: {\"event\": \"done\"}\n\n"
            return
        except Exception as e:
            logger.error(f'图谱执行失败：{e}')
            reply_error = _friendly_error(e)
            yield f"data: {json.dumps({'event': 'error', 'text': reply_error})}\n\n"
            yield "data: {\"event\": \"done\"}\n\n"
            return

        output_file = exec_state.get('output_file', '') or ''

        yield f"data: {json.dumps({'event': 'meta', 'output_file': output_file})}\n\n"

        yield f"data: {json.dumps({'event': 'status', 'text': '正在生成回答...'})}\n\n"

        chat_prompt = prompt_builder(exec_state)
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
            # 与图谱阶段一致：用可执行的提示替代原始异常
            reply_error = _friendly_error(e)
            yield f"data: {json.dumps({'event': 'error', 'text': reply_error})}\n\n"
            yield "data: {\"event\": \"done\"}\n\n"
            return

        yield "data: {\"event\": \"done\"}\n\n"

        if full_text:
            logger.info(f'AI回复：{full_text[:200]}')
        if output_file:
            logger.info(f'输出文件：{output_file}')
    finally:
        # 客户端断开/异常退出时兜底取消,确保后台 ffmpeg 与 LLM 任务不会继续空转
        if graph_task is not None and not graph_task.done() and stop_event is not None:
            stop_event.set()
            if job is not None:
                proc = job['proc'][0]
                if proc is not None and proc.poll() is None:
                    try:
                        proc.kill()
                    except OSError:
                        pass
        if job_id:
            _unregister_job(job_id)
        if conversation_id:
            try:
                append_turn(
                    conversation_id,
                    {'text': question, 'files': user_files or []},
                    # 错误只存进 error 字段，不再拼一份到 text：前端会把 error 渲染成
                    # 独立告警块，两处都存会让重新打开对话时同一条错误显示两遍。
                    {'text': full_text, 'output_file': output_file, 'error': reply_error},
                )
            except Exception as e:  # noqa: BLE001 - 落库失败不应影响已经推完的流
                logger.warning(f'写入对话记录失败（会话 {conversation_id}）：{e}')


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


def _build_context(history: list[str]) -> str:
    """把前端传来的多轮对话整理成上下文文本(最多 6 条、总长 4000 字符)。"""
    entries = [h.strip() for h in (history or []) if h and h.strip()]
    return '\n'.join(entries[-6:])[:4000]


def _sse_error(text: str) -> Response:
    """以 SSE 形式返回一条错误并结束（用于初始化中 / LLM 未配置等前置失败）。"""
    return Response(
        content=(f"data: {json.dumps({'event': 'error', 'text': text})}\n\n"
                 f"data: {json.dumps({'event': 'done'})}\n\n"),
        media_type="text/event-stream",
    )


def _selected_file_refs(selected: list) -> list:
    """把服务端选中的绝对路径转成前端用的 {name, src} 引用，用于历史回显。"""
    refs = []
    for path in selected or []:
        name = os.path.basename(path)
        try:
            inside_output = os.path.dirname(os.path.abspath(path)) == os.path.abspath(DOWNLOAD_DIR)
        except OSError:
            inside_output = False
        refs.append({'name': name, 'src': 'output' if inside_output else 'upload'})
    return refs


def _resolve_conversation(conversation_id: str):
    """取对话记录并算出本轮要用的 LLM 配置。

    返回 (record, llm_config, error_message)：
    - llm_config 为 None 表示用全局配置（对话没有覆盖）
    - error_message 非空表示对话不存在或覆盖配置非法，调用方应直接 SSE 报错
    """
    if not conversation_id:
        return None, None, ''
    record = get_conversation(conversation_id)
    if not record:
        return None, None, '对话不存在或已被删除，请在左侧重新选择一个对话'
    override = record.get('llm')
    if not override:
        # 没有覆盖也要把全局配置的"快照"带上吗？不带：全局配置改了（如换了模型）
        # 本对话应该立刻跟着变，这才是"继承"的语义。
        return record, None, ''
    try:
        cfg = merged_config(override)
    except ValueError as e:
        return record, None, f'本对话的模型配置无效：{e}'
    if not is_config_configured(cfg):
        return record, None, '本对话的模型配置不完整，请补全模型名称/接口地址/API Key'
    return record, cfg, ''


def _chat_response(question, files, history, *, kind, graph_fn, prompt_builder, spec,
                   mode='ffmpeg', conversation_id=''):
    """两个 chat 路由的公共实现（ffmpeg / ffprobe 仅参数不同）。"""
    if _init_state['status'] == 'running':
        return _sse_error('正在初始化知识库（首次运行需下载模型，请稍候）')

    record, llm_config, conv_error = _resolve_conversation(conversation_id)
    if conv_error:
        return _sse_error(conv_error)

    # 带对话级配置时按该配置构建/取用 agent；否则走全局 agent
    if not spec.ensure({'llm_config': llm_config}):
        return _sse_error('LLM 未配置，请先在页面右上角 ⚙️ 设置中填写模型信息')

    selected = _sanitize_selected_files(files)

    # 用户直接写出的 ffmpeg 参数（如「-crf 18」）必须原样保留，不被模型改写
    explicit = extract_explicit_params(question)

    logger.info(f'处理{kind}对话')
    logger.info(f'用户问题：{question[:200]}')
    logger.info(f'选择文件：{selected}')
    if conversation_id:
        logger.info(f'对话：{conversation_id}（模型：{(llm_config or {}).get("model") or "继承全局"}）')
    if explicit:
        logger.info(f'用户显式参数：{explicit}')

    context = _build_context(history)
    job_id, job = _register_job(kind)

    # 素材分析在主线程之外完成，但结果要注入图谱状态：
    # 用一个可变容器把 prep 的产出交给 graph_fn（graph_fn 在另一个线程里被调用）。
    analysis_box = {}

    def _prep(status_list):
        from app.media import analyze_files
        status_list.append('正在读取素材信息...')
        text = analyze_files(selected, question)
        analysis_box['text'] = text
        if text:
            status_list.append('已完成素材分析，开始生成命令...')
        return text

    def _run_graph(q, p):
        return graph_fn(q, p, files=selected, context=context,
                        stop_event=job['stop'], proc_box=job['proc'],
                        media_analysis=analysis_box.get('text', ''),
                        explicit_params=explicit,
                        llm_config=llm_config)

    return StreamingResponse(
        _event_stream(
            question,
            _run_graph,
            get_chat_agent(kind, llm_config),
            prompt_builder,
            job={'job_id': job_id, **job},
            prep_fn=_prep if selected else None,
            conversation_id=conversation_id,
            user_files=_selected_file_refs(selected),
        ),
        media_type="text/event-stream",
    )


@app.post("/api/chat")
async def chat(question: str = Form(...), files: list[str] = Form(default=[]),
               history: list[str] = Form(default=[]), conversation_id: str = Form(default='')):
    """发送问题 → 流式输出（ffmpeg search+execute 进度 + chat 逐 token）"""
    return _chat_response(
        question, files, history,
        kind='',
        graph_fn=exec_graph,
        prompt_builder=build_chat_prompt,
        spec=FFMPEG_GRAPH,
        mode='ffmpeg',
        conversation_id=conversation_id,
    )


@app.post("/api/probe/chat")
async def probe_chat(question: str = Form(...), files: list[str] = Form(default=[]),
                     history: list[str] = Form(default=[]), conversation_id: str = Form(default='')):
    """发送问题 → 流式输出（ffprobe search+execute 进度 + chat 逐 token）"""
    return _chat_response(
        question, files, history,
        kind='ffprobe ',
        graph_fn=probe_exec_graph,
        prompt_builder=build_probe_chat_prompt,
        spec=PROBE_GRAPH,
        mode='ffprobe',
        conversation_id=conversation_id,
    )


@app.post("/api/chat/stop")
async def stop_chat(body: dict):
    """停止正在运行的任务：POST {"job_id": "..."}"""
    job_id = (body or {}).get('job_id', '')
    if not job_id:
        raise HTTPException(status_code=400, detail="缺少 job_id")
    # owner 由发起方持有（前端始终回传同一个 job_id），用于拒绝他人冒用 job_id 停止任务
    owner = (body or {}).get('owner', '') or job_id
    stopped = _stop_job(job_id, owner)
    logger.info(f'停止任务 {job_id}：{"已停止" if stopped else "任务不存在/已结束或归属不匹配"}')
    return {"stopped": stopped}


def _list_dir_newest_first(path: str) -> list[str]:
    """列出目录下的文件名，按修改时间倒序（最新在前）。

    os.listdir 的顺序由文件系统决定（NTFS 上约等于名字序），新产物会随机插在中间，
    用户难以发现刚生成的文件；按 mtime 倒序让上传/输出列表稳定地把最新项排在最前。
    """
    if not os.path.isdir(path):
        return []
    entries = []
    for name in os.listdir(path):
        full = os.path.join(path, name)
        try:
            if os.path.isfile(full):
                entries.append((os.path.getmtime(full), name))
        except OSError:
            # 列目录期间文件被删除/占用：跳过，不要让整个列表失败
            continue
    entries.sort(key=lambda t: (-t[0], t[1]))
    return [name for _, name in entries]


@app.get("/api/output")
async def list_output():
    """列出 download/ 中的已完成文件（最新在前）"""
    logger.info('列出已完成文件')
    files = _list_dir_newest_first(DOWNLOAD_DIR)
    logger.info(f'文件列表：{files}')
    return {"files": files}


@app.delete("/api/output/{filename:path}")
async def delete_output(filename: str):
    """删除 download/ 中的已完成文件"""
    logger.info('删除已完成文件')
    logger.info(f'文件名：{filename}')
    path = _safe_path(DOWNLOAD_DIR, filename)
    if not path or not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="文件不存在")
    os.remove(path)
    return {"deleted": os.path.basename(path)}


@app.post("/api/output/delete")
async def batch_delete_output(body: dict):
    """批量删除输出文件：POST {"files": ["a.mp4", "b.jpg"]}"""
    files = body.get("files", [])
    results = {"deleted": [], "not_found": []}
    for f in files:
        path = _safe_path(DOWNLOAD_DIR, f)
        if path and os.path.isfile(path):
            os.remove(path)
            results["deleted"].append(os.path.basename(path))
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
            path = _safe_path(DOWNLOAD_DIR, f)
            if path and os.path.isfile(path):
                zf.write(path, arcname=os.path.basename(path))
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
    path = _safe_path(DOWNLOAD_DIR, filename)
    if not path or not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="文件不存在")
    return FileResponse(path)


@app.get("/api/upload")
async def list_uploaded():
    """列出 upload/ 中的文件（最新在前）"""
    logger.info('列出上传文件')
    files = _list_dir_newest_first(UPLOAD_DIR)
    logger.info(f'文件列表：{files}')
    return {"files": files}


@app.get("/api/upload/{filename:path}")
async def get_uploaded(filename: str):
    """返回 upload/ 中的文件供下载"""
    logger.info('下载上传文件')
    logger.info(f'文件名：{filename}')
    path = _safe_path(UPLOAD_DIR, filename)
    if not path or not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="文件不存在")
    return FileResponse(path)


@app.delete("/api/upload/{filename:path}")
async def delete_uploaded(filename: str):
    """删除 upload/ 中的文件"""
    logger.info('删除上传文件')
    logger.info(f'文件名：{filename}')
    path = _safe_path(UPLOAD_DIR, filename)
    if not path or not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="文件不存在")
    os.remove(path)
    logger.info(f'删除成功：{os.path.basename(path)}')
    return {"deleted": os.path.basename(path)}

from fastapi.staticfiles import StaticFiles


# ── LLM 设置 ──
from app.model import (
    get_model_config, update_model_config, merged_config, mask_config,
    is_config_configured, build_model_for,
)
# 用户显式写出的 ffmpeg 参数（如 -crf 18）需要原样保留，不被模型改写
from app.media import extract_explicit_params
# 多对话：会话记录持久化 + 对话级模型覆盖
from app.conversations import (
    append_turn, list_conversations, create_conversation, get_conversation,
    rename_conversation, set_llm_override, delete_conversation, stats as conversation_stats,
)
from app.graph import FFMPEG_GRAPH, PROBE_GRAPH


@app.get("/api/settings/llm")
async def get_llm_settings():
    # get_model_config() 每次都返回新 dict（api_key 已脱敏），无需再维护一份副本：
    # 旧实现的 _settings_store 只做 update，陈旧字段会一直残留。
    return get_model_config()


@app.put("/api/settings/llm")
async def update_llm_settings(body: dict):
    try:
        update_model_config(body)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # 保存后立刻做一次轻量连通性检查：配置写错（密钥失效/模型名不存在/地址不通）
    # 在这里就能发现并回传原因，而不是等到用户提问时在对话里报错。
    cfg = get_model_config()
    if cfg.get('configured'):
        check = await _check_llm_connection()
        if not check['ok']:
            # 配置已落盘（用户可能就是想先存着），但明确告知校验未通过
            raise HTTPException(status_code=400, detail=check['message'])
    return cfg


# ── 多对话 ──

def _conversation_view(record: dict) -> dict:
    """给前端的对话详情：消息 + 脱敏后的覆盖配置 + 实际生效配置。"""
    out = {
        'id': record['id'],
        'title': record.get('title') or '新对话',
        'mode': record.get('mode') or 'ffmpeg',
        'created_at': record.get('created_at') or 0,
        'updated_at': record.get('updated_at') or 0,
        'messages': record.get('messages') or [],
        'llm_override': None,
        'llm_effective': None,
    }
    override = record.get('llm')
    if override:
        view = dict(override)
        if view.get('api_key'):
            view['api_key'] = mask_key(view['api_key'])
        out['llm_override'] = view
    try:
        out['llm_effective'] = mask_config(merged_config(override))
    except ValueError as e:
        out['llm_effective'] = {'configured': False, 'error': str(e)}
    return out


@app.get("/api/conversations")
async def list_conversations_route(mode: str = ''):
    """列出对话（最新更新在前）；mode 为空时返回全部模式。"""
    return {"conversations": list_conversations(mode)}


@app.post("/api/conversations")
async def create_conversation_route(body: dict = None):
    body = body or {}
    record = create_conversation(body.get('mode', 'ffmpeg'), body.get('title', ''))
    return _conversation_view(record)


@app.get("/api/conversations/{cid}")
async def get_conversation_route(cid: str):
    record = get_conversation(cid)
    if not record:
        raise HTTPException(status_code=404, detail='对话不存在')
    return _conversation_view(record)


@app.patch("/api/conversations/{cid}")
async def update_conversation_route(cid: str, body: dict):
    """改名 / 设置对话级模型覆盖。

    body = {"title": "..."} 改名；
    body = {"llm": {...}}   设置覆盖（字段级，未给的仍继承全局）；
    body = {"llm": null}    清除覆盖，恢复继承全局。
    """
    body = body or {}
    if not get_conversation(cid):
        raise HTTPException(status_code=404, detail='对话不存在')

    if 'title' in body:
        if not rename_conversation(cid, body.get('title') or ''):
            raise HTTPException(status_code=404, detail='对话不存在')

    if 'llm' in body:
        override = body.get('llm')
        if override:
            if not isinstance(override, dict):
                raise HTTPException(status_code=400, detail='llm 必须是对象或 null')
            # 先按合并后的结果校验：temperature 越界、base_url 非法等在这里拦下
            try:
                effective = merged_config(override)
            except ValueError as e:
                raise HTTPException(status_code=400, detail=str(e))
            if not set_llm_override(cid, override):
                raise HTTPException(status_code=404, detail='对话不存在')
            # 覆盖里改了连接相关字段时做一次连通性检查（与全局设置一致：先落盘再报错）
            if any(k in override for k in ('model', 'base_url', 'api_key')):
                check = await _check_llm_connection(effective)
                if not check['ok']:
                    raise HTTPException(status_code=400, detail=check['message'])
        else:
            if not set_llm_override(cid, None):
                raise HTTPException(status_code=404, detail='对话不存在')

    record = get_conversation(cid)
    return _conversation_view(record)


@app.delete("/api/conversations/{cid}")
async def delete_conversation_route(cid: str):
    if not delete_conversation(cid):
        raise HTTPException(status_code=404, detail='对话不存在')
    return {"deleted": cid}


async def _check_llm_connection(cfg: dict = None) -> dict:
    """用 1 个 token 试调一次，返回 {'ok': bool, 'message': str}。

    cfg 为空时测全局配置，否则测给定配置（对话级覆盖）。
    """
    try:
        from langchain.messages import HumanMessage

        m = build_model_for(cfg) if cfg else get_model()
        if m is None:
            return {'ok': False, 'message': '模型未构建（配置不完整）'}
        await m.ainvoke([HumanMessage(content='ping')], config={'max_tokens': 1})
        logger.info('LLM 连通性检查通过')
        return {'ok': True, 'message': '配置有效，连接正常'}
    except Exception as e:  # noqa: BLE001 - 任何异常都要转成可读提示
        logger.warning(f'LLM 连通性检查失败：{e}')
        return {'ok': False, 'message': _friendly_error(e)}


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
