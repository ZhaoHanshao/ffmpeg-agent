import os, shutil
from functools import lru_cache
from dotenv import load_dotenv
from sentence_transformers import SentenceTransformer
from langchain_chroma import Chroma
from langchain_core.embeddings import Embeddings

load_dotenv()

DB_DIR = os.getenv('DB_DIR')
COLLECTION_NAME = os.getenv('COLLECTION_NAME')
BGE_CACHE_DIR = os.getenv('BGE_CACHE_DIR', 'backend/data/bge_small')
BGE_MODEL_NAME = os.getenv('BGE_MODEL_NAME', 'BAAI/bge-small-zh-v1.5')
HF_ENDPOINT = os.getenv('HF_ENDPOINT', '')


class BGEEmbedding(Embeddings):

    def __init__(self, path=None, model_name=None):
        super().__init__()
        path = path or BGE_CACHE_DIR
        model_name = model_name or BGE_MODEL_NAME

        if not os.path.isdir(path) or not os.listdir(path):
            print(f'正在下载嵌入模型 {model_name} 到 {path} ...')
            os.makedirs(path, exist_ok=True)
            if HF_ENDPOINT:
                os.environ['HF_ENDPOINT'] = HF_ENDPOINT
            temp = SentenceTransformer(model_name)
            temp.save(path)

        self.model = SentenceTransformer(path)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        embeddings = self.model.encode(texts, normalize_embeddings=True)
        return embeddings.tolist()

    def embed_query(self, text: str) -> list[float]:
        embeddings = self.model.encode([text], normalize_embeddings=True)
        return embeddings.tolist()[0]


def get_embeddings() -> Embeddings:
    return BGEEmbedding(path=BGE_CACHE_DIR)


@lru_cache(maxsize=1)
def _get_vector_db():
    print('=' * 20 + '加载向量数据库' + '=' * 20)
    return Chroma(
        persist_directory=DB_DIR,
        embedding_function=get_embeddings(),
        collection_name=COLLECTION_NAME,
    )


def _ensure_vector_db():
    db_file = os.path.join(DB_DIR, 'chroma.sqlite3') if DB_DIR else ''
    if DB_DIR and os.path.isfile(db_file):
        print('向量库已存在，跳过构建')
        return

    print('=' * 20 + '首次构建向量库' + '=' * 20)

    DOC_URL = os.getenv('DOC_URL', 'https://ffmpeg.org/ffmpeg-all.html')

    from app.build_vector_db import fetch_and_chunk
    chunks = fetch_and_chunk(DOC_URL)
    if not chunks:
        print('警告: 未获取到文档内容，向量库构建失败')
        return

    if DB_DIR and os.path.isdir(DB_DIR):
        shutil.rmtree(DB_DIR)

    embeddings = get_embeddings()
    Chroma.from_documents(
        documents=chunks,
        embedding=embeddings,
        persist_directory=DB_DIR,
        collection_name=COLLECTION_NAME,
    )
    print('=' * 20 + '向量库构建完成' + '=' * 20)


def get_text(question: str):
    print('=' * 20 + '向量检索' + '=' * 20)
    print(f'检索内容：{question[:200]}')
    try:
        result = _get_vector_db().similarity_search(query=question, k=5)
        return [doc.page_content for doc in result]
    except Exception as e:
        return f'查询失败，原因:\n{e}'


if __name__ == '__main__':
    result = get_text('how to invert colors of an image?')
    for i, doc in enumerate(result, 1):
        print(f'来源[{i}]')
        print(f'内容{doc[:50]}')
