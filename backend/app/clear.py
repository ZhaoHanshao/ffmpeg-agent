import os, shutil
from dotenv import load_dotenv

load_dotenv()

DOWNLOAD = os.getenv('DOWNLOAD')

def clear_dir(dir: str = DOWNLOAD):
    if not os.path.exists(dir):
        os.makedirs(dir, exist_ok=True)
    else:
        shutil.rmtree(dir)
        os.makedirs(dir, exist_ok=True)
