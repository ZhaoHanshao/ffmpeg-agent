from langchain_chroma import Chroma
from langchain_core.embeddings import Embeddings
from sentence_transformers import SentenceTransformer
from dotenv import load_dotenv
import os

load_dotenv()

DB_DIR = os.getenv('DB_DIR')
COLLECTION_NAME = os.getenv('COLLECTION_NAME')
DBG = os.getenv('DBG')


class BGEEmbedding(Embeddings):

    def __init__(self, path=DBG):
        super().__init__()
        self.model = SentenceTransformer(path)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        embeddings = self.model.encode(texts, normalize_embeddings=True)
        return embeddings.tolist()

    def embed_query(self, text: str) -> list[float]:
        embeddings = self.model.encode([text], normalize_embeddings=True)
        return embeddings.tolist()[0]


def get_embeddings() -> Embeddings:
    return BGEEmbedding(path=DBG)


print('=' * 20 + '加载向量数据库' + '=' * 20)
embeddings = get_embeddings()

vector_db = Chroma(
    persist_directory=DB_DIR,
    embedding_function=embeddings,
    collection_name=COLLECTION_NAME,
)


def get_text(question: str):
    print('=' * 20 + '向量检索' + '=' * 20)
    print(f'检索内容：{question[:200]}')
    try:
        result = vector_db.similarity_search(query=question, k=5)
        return [doc.page_content for doc in result]
    except Exception as e:
        return f'查询失败，原因:\n{e}'


if __name__ == '__main__':
    result = get_text('如何将图片反色？')
    for i, doc in enumerate(result, 1):
        print(f'来源[{i}]')
        print(f'内容{doc[0:50]}')
