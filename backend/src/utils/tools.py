from langchain.tools import tool
from langchain_core.runnables import RunnableConfig
from utils.tool.db_search import get_text
from dotenv import load_dotenv
import os,subprocess
from tool.clear import clear_dir 

load_dotenv()
DOWNLOAD = os.getenv('DOWNLOAD')
UPLOAD = os.getenv('UPLOAD')

@tool
def get_commend(squry:str):
    '''
    根据用户的问题查询ffmpeg文档中相关的内容
    squry:用户的问题
    返回的结果为列表，包括按相关度排序的序号和具体内容
    '''
    result = get_text(squry)
    contents = []
    for i,doc in enumerate(result,1):
        content = f'来源[{i}]，{doc}'
        contents.append(content)
    return contents

@tool
def get_files(config:RunnableConfig):
    '''
    全部读取
    获取将要执行ffmpeg命令的文件
    文件数量为一个或者多个，返回结果是一个列表
    返回结果：第一个为要处理的文件，即在ffmpeg命令中 -i 后跟着的input
             第二个为处理后的文件存放的地址
    '''
    files = os.listdir(UPLOAD)
    for i, file in enumerate(files,0):
        files[i] = f'{UPLOAD}\{file}'
    return {
        "需要处理":files,
        "存放地址":UPLOAD
    } #返回给LLM读取的
    

@tool
def excute_command(command:str):
    '''
    执行ffmpeg命令
    参数值：
    command:标准的终端ffmpeg执行命令，例如:ffmpeg -i input.mp4 output.avi
    返回中文常规执行结果
    '''
    exit_code = subprocess.run(args=command,capture_output=True)
    if exit_code.returncode==0:
        return {
            'command':command,
            'flag':True
        }
    else:
        return {
            'command':command,
            'command_result':f'{command}执行失败：{exit_code.stderr}'
        }



# 要学的东西，tool返回结果为字典可以修改state