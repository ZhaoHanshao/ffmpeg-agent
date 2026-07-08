from dotenv import load_dotenv
import os
from langchain_openai import ChatOpenAI

load_dotenv()

MODEL_NAME = os.getenv('MODEL_NAME')
BASE_URL = os.getenv('BASE_URL')
API_KEY = os.getenv('API_KEY')

model = ChatOpenAI(
    model=MODEL_NAME,
    base_url=BASE_URL,
    api_key=API_KEY,
    temperature=0.2,
    max_tokens=2048,
    streaming=True,
)
