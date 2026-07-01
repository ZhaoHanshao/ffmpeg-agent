import os, uuid, shutil
from dotenv import load_dotenv
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware

load_dotenv()

UPLOAD_DIR = os.getenv('UPLOAD', 'backend/upload')
DOWNLOAD_DIR = os.getenv('DOWNLOAD', 'backend/download')

app = FastAPI(title="ffmpeg-agent")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _clear_dir(path: str):
    if os.path.exists(path):
        shutil.rmtree(path)
    os.makedirs(path, exist_ok=True)


def _save_with_uuid(file: UploadFile) -> str:
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    name = f"{uuid.uuid4().hex}_{file.filename}"
    with open(os.path.join(UPLOAD_DIR, name), 'wb') as f:
        f.write(file.file.read())
    return name


@app.post("/api/upload")
async def upload_files(files: list[UploadFile] = File(...)):
    """上传一个或多个文件到 upload/，之前的文件会被清除"""
    _clear_dir(UPLOAD_DIR)
    saved = [_save_with_uuid(f) for f in files]
    return {"uploaded": saved}


@app.post("/api/chat")
async def chat(question: str = Form(...)):
    """发送问题，运行 graph，自动使用 upload/ 中的文件"""
    from app.graph import run_graph
    result = run_graph(question)
    if os.path.exists(UPLOAD_DIR):
        shutil.rmtree(UPLOAD_DIR)
    return {
        "reply": result["reply"],
        "output_file": result["output_file"] or None,
    }


@app.get("/api/output/{filename:path}")
async def get_output(filename: str):
    """返回 download/ 中的文件"""
    path = os.path.join(DOWNLOAD_DIR, filename)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="文件不存在")
    return FileResponse(path)


if __name__ == '__main__':
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
