"""打包期预构建 Chroma 向量库（离线，避免运行时依赖 ffmpeg.org）。

优先使用 backend/data/docs/ 下的本地文档缓存；缺失时退化为联网抓取。
输出：backend/data/chroma_db（ffmpeg_docs + ffprobe_docs 两个 collection，
由 ONNX 嵌入器生成向量，与运行时一致）。

用法：.venv\\Scripts\\python.exe backend\\build_package_db.py
"""
import os
import sys

sys.path.insert(0, 'backend')

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS = os.path.join(ROOT, 'backend', 'data', 'docs')
DB = os.path.join(ROOT, 'backend', 'data', 'chroma_db')

local_ffmpeg = os.path.join(DOCS, 'ffmpeg-all.html')
local_probe = os.path.join(DOCS, 'ffprobe-all.html')

os.environ['DB_DIR'] = DB
os.environ['DOC_URL'] = local_ffmpeg if os.path.isfile(local_ffmpeg) else 'https://ffmpeg.org/ffmpeg-all.html'
os.environ['PROBE_DOC_URL'] = local_probe if os.path.isfile(local_probe) else 'https://ffmpeg.org/ffprobe-all.html'
os.environ.setdefault('BGE_CACHE_DIR', os.path.join(ROOT, 'backend', 'data', 'bge_onnx'))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from app.db_search import _ensure_vector_db, _ensure_probe_vector_db  # noqa: E402

if __name__ == '__main__':
    print(f'ffmpeg 文档源: {os.environ["DOC_URL"]}')
    print(f'ffprobe 文档源: {os.environ["PROBE_DOC_URL"]}')
    _ensure_vector_db()
    _ensure_probe_vector_db()
    print(f'向量库就绪: {DB}')