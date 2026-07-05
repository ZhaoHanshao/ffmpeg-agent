import os, sys, shutil, json, atexit, io, zipfile, datetime
from dotenv import load_dotenv
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse, Response
from fastapi.middleware.cors import CORSMiddleware

# 确保 CWD 指向项目根目录，使后续 import 和 load_dotenv() 的路径正确
_backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(_backend_dir))

# 确保 backend/ 在 sys.path 中，使 app 包可被 import
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

from app.graph import exec_graph, build_chat_prompt
from app.agents import agent_chat
from langchain.messages import HumanMessage

load_dotenv()

UPLOAD_DIR = os.getenv('UPLOAD', 'backend/upload')
DOWNLOAD_DIR = os.getenv('DOWNLOAD', 'backend/download')

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


@app.on_event("startup")
def preload_models():
    print('=' * 20 + '预加载模型和向量库' + '=' * 20)
    from app.db_search import _ensure_vector_db, _get_vector_db
    _ensure_vector_db()
    _get_vector_db()
    print('=' * 20 + '预加载完成' + '=' * 20)


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
    print('=' * 20 + '上传文件' + '=' * 20)
    print(f'文件数量：{len(files)}')
    saved = []
    counter = {}
    for f in files:
        name = f.filename or "file"
        counter[name] = counter.get(name, 0) + 1
        saved.append(_save_with_timestamp(f, counter[name]))
    print(f'保存文件：{saved}')
    return {"uploaded": saved}


@app.post("/api/chat")
async def chat(question: str = Form(...)):
    """发送问题 → 执行 search+execute 循环 → 流式输出 chat 回复"""
    print('=' * 20 + '处理对话' + '=' * 20)
    print(f'用户问题：{question[:200]}')

    # Step 1: 执行 search+execute 循环（同步）
    exec_state = exec_graph(question)
    output_file = exec_state.get('output_file', '') or ''

    # Step 2: 构建 chat prompt
    chat_prompt = build_chat_prompt(exec_state)

    # Step 3: 流式输出 chat agent 的回复（SSE）
    async def event_stream():
        # 先发 meta 事件（输出文件信息）
        yield f"data: {json.dumps({'event': 'meta', 'output_file': output_file})}\n\n"

        full_text = ''
        try:
            async for event in agent_chat.astream_events(
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
            print(f'AI回复：{full_text[:200]}')
        if output_file:
            print(f'输出文件：{output_file}')

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.get("/api/output")
async def list_output():
    """列出 download/ 中的已完成文件"""
    print('=' * 20 + '列出已完成文件' + '=' * 20)
    if not os.path.exists(DOWNLOAD_DIR):
        return {"files": []}
    files = [f for f in os.listdir(DOWNLOAD_DIR) if os.path.isfile(os.path.join(DOWNLOAD_DIR, f))]
    print(f'文件列表：{files}')
    return {"files": files}


@app.delete("/api/output/{filename:path}")
async def delete_output(filename: str):
    """删除 download/ 中的已完成文件"""
    print('=' * 20 + '删除已完成文件' + '=' * 20)
    print(f'文件名：{filename}')
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
    print('=' * 20 + '下载已完成文件' + '=' * 20)
    print(f'文件名：{filename}')
    path = os.path.join(DOWNLOAD_DIR, filename)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="文件不存在")
    return FileResponse(path)


@app.get("/api/upload")
async def list_uploaded():
    """列出 upload/ 中的文件"""
    print('=' * 20 + '列出上传文件' + '=' * 20)
    if not os.path.exists(UPLOAD_DIR):
        return {"files": []}
    files = [f for f in os.listdir(UPLOAD_DIR) if os.path.isfile(os.path.join(UPLOAD_DIR, f))]
    print(f'文件列表：{files}')
    return {"files": files}


@app.get("/api/upload/{filename:path}")
async def get_uploaded(filename: str):
    """返回 upload/ 中的文件供下载"""
    print('=' * 20 + '下载上传文件' + '=' * 20)
    print(f'文件名：{filename}')
    path = os.path.join(UPLOAD_DIR, filename)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="文件不存在")
    return FileResponse(path)


@app.delete("/api/upload/{filename:path}")
async def delete_uploaded(filename: str):
    """删除 upload/ 中的文件"""
    print('=' * 20 + '删除上传文件' + '=' * 20)
    print(f'文件名：{filename}')
    path = os.path.join(UPLOAD_DIR, filename)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="文件不存在")
    os.remove(path)
    print(f'删除成功：{filename}')
    return {"deleted": filename}

from fastapi.staticfiles import StaticFiles
app.mount("/", StaticFiles(directory="frontend/dist", html=True), name="frontend")

if __name__ == '__main__':
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
