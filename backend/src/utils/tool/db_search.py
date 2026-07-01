from langchain_chroma import Chroma
from pathlib import Path
from langchain_core.embeddings import Embeddings
from sentence_transformers import SentenceTransformer

from dotenv import load_dotenv
import os

load_dotenv()

DB_DIR = os.getenv('DB_DIR')
COLLECTION_NAME = os.getenv('COLLECTION_NAME')

class BGEEmbedding(Embeddings):

    def __init__(self, path='/home/shaohan/my_file/code/work/work-agent/test-agent/01-RAG知识库制作/data/bge_small'):
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

embeddings = get_embeddings()

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