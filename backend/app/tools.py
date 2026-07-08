from langchain.tools import tool
from langchain_core.runnables import RunnableConfig
from app.db_search import get_text
from dotenv import load_dotenv
import os, subprocess, shlex

load_dotenv()

DOWNLOAD = os.getenv('DOWNLOAD', 'backend/download')
UPLOAD = os.getenv('UPLOAD', 'backend/upload')


@tool
def get_command(squry: str):
    """
    根据用户的问题查询ffmpeg文档中相关的内容
    squry:用户的问题
    返回的结果为列表，包括按相关度排序的序号和具体内容
    """
    print('=' * 20 + '查询知识库' + '=' * 20)
    print(f'查询内容：{squry[:200]}')
    result = get_text(squry)
    contents = []
    for i, doc in enumerate(result, 1):
        content = f'来源[{i}]，{doc}'
        contents.append(content)
    return contents


@tool
def get_files(config: RunnableConfig):
    """
    全部读取
    获取将要执行ffmpeg命令的文件
    文件数量为一个或者多个，返回结果是一个列表
    返回结果：第一个为要处理的文件，即在ffmpeg命令中 -i 后跟着的input
              第二个为处理后的文件存放的地址
    """
    print('=' * 20 + '获取文件列表' + '=' * 20)
    os.makedirs(UPLOAD, exist_ok=True)
    files = os.listdir(UPLOAD)
    for i, file in enumerate(files, 0):
        files[i] = os.path.join(UPLOAD, file)
    return {
        "需要处理": files,
        "输入目录": UPLOAD,
        "输出目录": DOWNLOAD,
    }


@tool
def execute_command(command: str):
    """
    执行ffmpeg命令
    参数值：
    command:标准的终端ffmpeg执行命令，例如:ffmpeg -i input.mp4 output.avi
    返回中文常规执行结果
    """
    print('=' * 20 + '执行命令' + '=' * 20)
    print(f'原始命令：{command}')

    # 安全校验：只允许以 ffmpeg 开头的命令
    cmd_name = shlex.split(command)[0]
    if cmd_name != 'ffmpeg':
        print(f'拒绝执行非 ffmpeg 命令：{cmd_name}')
        return {
            'command': command,
            'command_result': f'拒绝执行非 ffmpeg 命令：{cmd_name}。请直接使用 ffmpeg 命令完成任务。',
        }

    # 将输出路径强制重写到 DOWNLOAD 目录
    parts = shlex.split(command)
    output_idx = None
    for i in range(len(parts) - 1, -1, -1):
        if i == 0:
            continue  # 跳过命令名
        if parts[i].startswith('-'):
            continue  # 跳过标志参数
        output_idx = i
        break

    if output_idx is not None:
        original = parts[output_idx]
        # 仅当路径尚未指向 DOWNLOAD 时才重写
        if DOWNLOAD not in original and DOWNLOAD not in os.path.dirname(original):
            parts[output_idx] = os.path.join(DOWNLOAD, os.path.basename(original))
            command = shlex.join(parts)
            print(f'输出路径已重写至 {DOWNLOAD}/')

    print(f'执行命令：{command}')
    os.makedirs(DOWNLOAD, exist_ok=True)
    try:
        exit_code = subprocess.run(args=shlex.split(command), capture_output=True)
        if exit_code.returncode == 0:
            return {'command': command, 'flag': True, 'command_result': f'{command} 执行成功'}
        else:
            return {
                'command': command,
                'command_result': f'{command} 执行失败：{exit_code.stderr.decode(errors="replace")}',
            }
    except OSError as e:
        return {
            'command': command,
            'command_result': f'命令执行异常：{e}',
        }
