import os
from dotenv import load_dotenv
import subprocess

load_dotenv()

UPLOAD = os.getenv('UPLOAD')
DOWNLOAD  = os.getenv('DOWNLOAD')
files = os.listdir(UPLOAD)
for file in files:
    print(file)

if not files:
    print('upload 目录为空')
    exit()

file = files[0]

command = f'ffmpeg -i {os.path.join(UPLOAD, file)} output {os.path.join(DOWNLOAD, "output.jpg")}'

# 测试命令执行
exit_code = subprocess.run(command,capture_output=True)

print(exit_code)