from langchain_chroma import Chroma
from pathlib import Path
from langchain_core.embeddings import Embeddings
from sentence_transformers import SentenceTransformer

from dotenv import load_dotenv
import os

load_dotenv()

DB_DIR = os.getenv('DB_DIR')
COLLECTION_NAME = os.getenv('COLLECTION_NAME')
DBG = os.getenv('DBG')

class BGEEmbedding(Embeddings):

    def __init__(self, path=DBG):  # TODO: 默认参数 hardcode 了另一个项目的路径。DB_DIR 环境变量缺失时会报错，应移除默认值或使用本项目的 data/bge_small 路径
        super().__init__()
        self.model = SentenceTransformer(path)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        embeddings = self.model.encode(texts, normalize_embeddings=True)
        return embeddings.tolist()

    def embed_query(self, text: str) -> list[float]:
        embeddings = self.model.encode([text], normalize_embeddings=True)
        return embeddings.tolist()[0]

def get_embeddings() -> Embeddings:
    '''
    调用本地模型
    '''
    return BGEEmbedding(path=DB_DIR)

embeddings = get_embeddings()  # TODO: 全局作用域直接加载 embedding 模型（~200MB），模块 import 时即阻塞。应惰性加载或异步初始化

# TODO: 全局作用域直接执行 Chroma() 初始化，模块加载时若环境变量缺失会直接崩溃。应延迟初始化或使用惰性加载
vector_db = Chroma(
    persist_directory=DB_DIR, 
    embedding_function=embeddings,
    collection_name=COLLECTION_NAME,
)
def get_text(question:str):
    try:
        result = vector_db.similarity_search(query=question,k=5)
        res_list = []
        for doc in result:
            # section = doc.metadata.get('section','?')
            text = doc.page_content
            res_list.append(text)
    except Exception as e:
        return f'查询失败，原因:\n{e}'
    return res_list
        
if __name__=='__main__':
    result = get_text('如何将图片反色？')

    for i,doc in enumerate(result,1):
        print(f'来源[{i}]')
        print(f'内容{doc[0:50]}')