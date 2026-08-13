import os, shutil, logging, json, time
from functools import lru_cache
from dotenv import load_dotenv
from app.onnx_embed import BGEOnnxEmbedding, resolve_model_dir
from langchain_chroma import Chroma
from langchain_core.embeddings import Embeddings

load_dotenv()

logger = logging.getLogger(__name__)

DB_DIR = os.getenv('DB_DIR')
COLLECTION_NAME = os.getenv('COLLECTION_NAME')
PROBE_COLLECTION_NAME = os.getenv('PROBE_COLLECTION_NAME', 'ffprobe_docs')
BGE_CACHE_DIR = os.getenv('BGE_CACHE_DIR', 'backend/data/bge_onnx')

# 文档自动刷新：DOC_REFRESH_DAYS > 0 时,向量库构建超过该天数会在启动时自动重建
try:
    DOC_REFRESH_DAYS = int(os.getenv('DOC_REFRESH_DAYS', '0') or 0)
except ValueError:
    DOC_REFRESH_DAYS = 0


def _marker_path(collection_name: str) -> str:
    return os.path.join(DB_DIR, f'.built_{collection_name}.json') if DB_DIR else ''


def _mark_built(collection_name: str):
    marker = _marker_path(collection_name)
    if not marker:
        return
    try:
        os.makedirs(DB_DIR, exist_ok=True)
        with open(marker, 'w', encoding='utf-8') as f:
            json.dump({'built_at': time.time()}, f)
    except OSError as e:
        logger.warning(f'写入构建标记失败：{e}')


def _is_stale(collection_name: str) -> bool:
    """按 DOC_REFRESH_DAYS 判断该 collection 是否需要重建。"""
    if DOC_REFRESH_DAYS <= 0:
        return False
    marker = _marker_path(collection_name)
    if not marker:
        return False
    if not os.path.isfile(marker):
        # 老版本没有标记：视为刚构建,补写标记,避免升级后强制重抓文档
        _mark_built(collection_name)
        return False
    try:
        with open(marker, 'r', encoding='utf-8') as f:
            built = float(json.load(f).get('built_at', 0))
    except (OSError, ValueError):
        built = 0
    return (time.time() - built) > DOC_REFRESH_DAYS * 86400


class BGEEmbedding(Embeddings):

    def __init__(self, path=None):
        super().__init__()
        self.model = BGEOnnxEmbedding(path or resolve_model_dir())

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self.model.embed_documents(texts)

    def embed_query(self, text: str) -> list[float]:
        return self.model.embed_query(text)


def get_embeddings() -> Embeddings:
    return BGEEmbedding()


@lru_cache(maxsize=1)
def _get_vector_db():
    logger.info('加载向量数据库')
    return Chroma(
        persist_directory=DB_DIR,
        embedding_function=get_embeddings(),
        collection_name=COLLECTION_NAME,
    )


def _ensure_vector_db():
    db_file = os.path.join(DB_DIR, 'chroma.sqlite3') if DB_DIR else ''
    if DB_DIR and os.path.isfile(db_file) and not _is_stale(COLLECTION_NAME):
        logger.info('向量库已存在，跳过构建')
        return

    logger.info('首次构建或刷新向量库')

    DOC_URL = os.getenv('DOC_URL', 'https://ffmpeg.org/ffmpeg-all.html')

    from app.build_vector_db import fetch_and_chunk
    chunks = fetch_and_chunk(DOC_URL)
    if not chunks:
        logger.warning('警告: 未获取到文档内容，向量库构建失败')
        return

    if DB_DIR and os.path.isfile(db_file):
        # 刷新场景：只删除本 collection(保留同库的 ffprobe 等),避免整库重建
        try:
            Chroma(
                persist_directory=DB_DIR,
                embedding_function=get_embeddings(),
                collection_name=COLLECTION_NAME,
            ).delete_collection()
        except Exception as e:
            logger.warning(f'删除旧 collection 失败(将尝试覆盖)：{e}')
    elif DB_DIR and os.path.isdir(DB_DIR):
        shutil.rmtree(DB_DIR)

    embeddings = get_embeddings()
    Chroma.from_documents(
        documents=chunks,
        embedding=embeddings,
        persist_directory=DB_DIR,
        collection_name=COLLECTION_NAME,
    )
    _get_vector_db.cache_clear()
    _mark_built(COLLECTION_NAME)
    logger.info('向量库构建完成')


def get_text(question: str):
    logger.info('向量检索')
    logger.info(f'检索内容：{question[:200]}')
    try:
        docs = _get_vector_db().similarity_search(query=question, k=20)
        out, seen = [], set()
        for doc in docs:
            if doc.page_content in seen:
                continue
            seen.add(doc.page_content)
            out.append({'title': doc.metadata.get('title', ''), 'content': doc.page_content})
        return out
    except Exception as e:
        return f'查询失败，原因:\n{e}'


# ── ffprobe 知识库（ffprobe-all.html） ──

@lru_cache(maxsize=1)
def _get_probe_vector_db():
    logger.info('加载 ffprobe 向量数据库')
    return Chroma(
        persist_directory=DB_DIR,
        embedding_function=get_embeddings(),
        collection_name=PROBE_COLLECTION_NAME,
    )


def _ensure_probe_vector_db():
    db_file = os.path.join(DB_DIR, 'chroma.sqlite3') if DB_DIR else ''
    if DB_DIR and os.path.isfile(db_file) and not _is_stale(PROBE_COLLECTION_NAME):
        try:
            collections = [c.name for c in _get_vector_db()._client.list_collections()]
            if PROBE_COLLECTION_NAME in collections:
                logger.info('ffprobe 向量库已存在，跳过构建')
                return
        except Exception as e:
            logger.warning(f'检查 ffprobe 向量库失败：{e}，跳过构建避免重复')
            return

    logger.info('首次构建或刷新 ffprobe 向量库')

    PROBE_DOC_URL = os.getenv('PROBE_DOC_URL', 'https://ffmpeg.org/ffprobe-all.html')

    from app.build_vector_db import fetch_and_chunk
    chunks = fetch_and_chunk(PROBE_DOC_URL)
    if not chunks:
        logger.warning('警告: 未获取到 ffprobe 文档内容，向量库构建失败')
        return

    if DB_DIR and os.path.isfile(db_file):
        # 刷新场景：仅删除本 collection
        try:
            Chroma(
                persist_directory=DB_DIR,
                embedding_function=get_embeddings(),
                collection_name=PROBE_COLLECTION_NAME,
            ).delete_collection()
        except Exception as e:
            logger.warning(f'删除旧 ffprobe collection 失败(将尝试覆盖)：{e}')

    embeddings = get_embeddings()
    Chroma.from_documents(
        documents=chunks,
        embedding=embeddings,
        persist_directory=DB_DIR,
        collection_name=PROBE_COLLECTION_NAME,
    )
    _get_probe_vector_db.cache_clear()
    _mark_built(PROBE_COLLECTION_NAME)
    logger.info('ffprobe 向量库构建完成')


def get_probe_text(question: str):
    logger.info('ffprobe 向量检索')
    logger.info(f'检索内容：{question[:200]}')
    try:
        docs = _get_probe_vector_db().similarity_search(query=question, k=20)
        out, seen = [], set()
        for doc in docs:
            if doc.page_content in seen:
                continue
            seen.add(doc.page_content)
            out.append({'title': doc.metadata.get('title', ''), 'content': doc.page_content})
        return out
    except Exception as e:
        return f'查询失败，原因:\n{e}'


if __name__ == '__main__':
    result = get_text('how to invert colors of an image?')
    for i, doc in enumerate(result, 1):
        logger.info(f'来源[{i}]')
        logger.info(f'内容{doc[:50]}')
