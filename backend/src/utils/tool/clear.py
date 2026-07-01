import os,shutil
from dotenv import load_dotenv

load_dotenv()

DOWNLOWD = os.getenv('DOWNLOAD')

def clear_dir(dir:str=DOWNLOWD):
    if not os.path.exists(dir): #判定是否存在，不存在则创建
        os.makedirs(dir,exist_ok=True)
    else:
        shutil.rmtree(dir)
        os.makedirs(dir,exist_ok=True)