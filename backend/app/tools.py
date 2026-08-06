from langchain.tools import tool
from langchain_core.runnables import RunnableConfig
from app.db_search import get_text, get_probe_text
from dotenv import load_dotenv
import os, sys, subprocess, shlex, logging

load_dotenv()

logger = logging.getLogger(__name__)

DOWNLOAD = os.getenv('DOWNLOAD', 'backend/download')
UPLOAD = os.getenv('UPLOAD', 'backend/upload')


def _is_frozen() -> bool:
    return bool(getattr(sys, 'frozen', False))


def ffmpeg_bin(name: str) -> str:
    """冻结模式下返回包内 ffmpeg/ffprobe 可执行文件路径，否则返回裸命令名。"""
    if _is_frozen():
        return os.path.join(sys._MEIPASS, 'ffmpeg', f'{name}.exe')
    return name


def split_command(cmd: str) -> list:
    """按 Windows 规则拆分命令，保留路径反斜杠并去掉引用引号。"""
    parts = shlex.split(cmd, posix=False)
    result = []
    for p in parts:
        if len(p) >= 2 and p[0] == '"' and p[-1] == '"':
            p = p[1:-1].replace('""', '"')
        result.append(p)
    return result


@tool
def get_command(squry: str):
    """
    根据用户的问题查询ffmpeg文档中相关的内容
    squry:用户的问题
    返回的结果为列表，包括按相关度排序的序号和具体内容
    """
    logger.info('查询知识库')
    logger.info(f'查询内容：{squry[:200]}')
    result = get_text(squry)
    contents = []
    for i, doc in enumerate(result, 1):
        content = f'来源[{i}]，{doc}'
        contents.append(content)
    return contents


@tool
def get_probe_command(squry: str):
    """
    根据用户的问题查询ffprobe文档中相关的内容
    squry:用户的问题
    返回的结果为列表，包括按相关度排序的序号和具体内容
    """
    logger.info('查询 ffprobe 知识库')
    logger.info(f'查询内容：{squry[:200]}')
    result = get_probe_text(squry)
    contents = []
    for i, doc in enumerate(result, 1):
        content = f'来源[{i}]，{doc}'
        contents.append(content)
    return contents


@tool
def get_files(config: RunnableConfig):
    """
    获取将要执行ffmpeg命令的文件
    文件数量为一个或者多个，返回结果是一个列表
    返回结果：第一个为要处理的文件，即在ffmpeg命令中 -i 后跟着的input
              第二个为处理后的文件存放的地址
    只返回用户选中的文件；未指定选中文件时返回全部
    """
    logger.info('获取文件列表')
    os.makedirs(UPLOAD, exist_ok=True)
    selected = ((config or {}).get('configurable') or {}).get('selected_files') or []
    if selected:
        files = list(dict.fromkeys(p for p in selected if os.path.isfile(p)))
    else:
        files = [os.path.join(UPLOAD, name) for name in os.listdir(UPLOAD)]
    return {
        "需要处理": files,
        "输入目录": UPLOAD,
        "输出目录": DOWNLOAD,
    }


@tool
def execute_command(command: str, config: RunnableConfig):
    """
    执行ffmpeg命令
    参数值：
    command:标准的终端ffmpeg执行命令，例如:ffmpeg -i input.mp4 output.avi
    返回中文常规执行结果
    """
    logger.info('执行命令')
    logger.info(f'原始命令：{command}')

    # 安全校验：只允许以 ffmpeg 开头的命令
    cmd_name = split_command(command)[0]
    if cmd_name != 'ffmpeg':
        logger.info(f'拒绝执行非 ffmpeg 命令：{cmd_name}')
        return {
            'command': command,
            'command_result': f'拒绝执行非 ffmpeg 命令：{cmd_name}。请直接使用 ffmpeg 命令完成任务。',
        }

    # 将输出路径强制重写到 DOWNLOAD 目录
    parts = split_command(command)
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
            command = subprocess.list2cmdline(parts)
            logger.info(f'输出路径已重写至 {DOWNLOAD}/')

    logger.info(f'执行命令：{command}')
    os.makedirs(DOWNLOAD, exist_ok=True)

    # 清空下载目录，防止 ffmpeg 阻塞在 Overwrite? [y/N] 提示
    # 本次选中的输入文件（可能来自下载目录）需要保留，不能被清掉
    protected = {os.path.normpath(p) for p in (((config or {}).get('configurable') or {}).get('selected_files') or [])}
    if os.path.exists(DOWNLOAD):
        for f in os.listdir(DOWNLOAD):
            fp = os.path.join(DOWNLOAD, f)
            if os.path.isfile(fp) and os.path.normpath(fp) not in protected:
                os.remove(fp)

    try:
        run_parts = split_command(command)
        run_parts[0] = ffmpeg_bin('ffmpeg')
        exit_code = subprocess.run(args=run_parts, capture_output=True)
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


@tool
def execute_probe_command(command: str):
    """
    执行ffprobe命令（只读分析工具，结果输出到标准输出）
    参数值：
    command:标准的终端ffprobe执行命令，例如:ffprobe -v error -show_format input.mp4
    返回中文常规执行结果
    """
    logger.info('执行 ffprobe 命令')
    logger.info(f'原始命令：{command}')

    # 安全校验：只允许以 ffprobe 开头的命令
    cmd_name = split_command(command)[0]
    if cmd_name != 'ffprobe':
        logger.info(f'拒绝执行非 ffprobe 命令：{cmd_name}')
        return {
            'command': command,
            'command_result': f'拒绝执行非 ffprobe 命令：{cmd_name}。请直接使用 ffprobe 命令完成任务。',
        }

    try:
        run_parts = split_command(command)
        run_parts[0] = ffmpeg_bin('ffprobe')
        proc = subprocess.run(args=run_parts, capture_output=True)
        if proc.returncode == 0:
            output = proc.stdout.decode(errors='replace').strip()
            return {
                'command': command,
                'flag': True,
                'command_result': output or f'{command} 执行成功',
            }
        else:
            return {
                'command': command,
                'command_result': f'{command} 执行失败：{proc.stderr.decode(errors="replace")}',
            }
    except OSError as e:
        return {
            'command': command,
            'command_result': f'命令执行异常：{e}',
        }
