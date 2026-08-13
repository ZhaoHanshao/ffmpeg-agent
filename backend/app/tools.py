from langchain.tools import tool
from langchain_core.runnables import RunnableConfig
from app.db_search import get_text, get_probe_text
from dotenv import load_dotenv
import os, sys, subprocess, shlex, logging, time, tempfile, threading

load_dotenv()

logger = logging.getLogger(__name__)

DOWNLOAD = os.getenv('DOWNLOAD', 'backend/download')
UPLOAD = os.getenv('UPLOAD', 'backend/upload')

# 串行化"清空下载目录 + 执行命令"整段临界区，避免并发请求互相删除对方的输入/输出文件
_execute_lock = threading.Lock()

# 单条命令最长执行时间(秒),超时强制终止,防止失控命令占死线程
try:
    EXEC_TIMEOUT = int(os.getenv('FFMPEG_TIMEOUT', '1800'))
except ValueError:
    EXEC_TIMEOUT = 1800

# 允许作为 -i 输入的 lavfi 虚拟源(无真实文件路径)
VIRTUAL_SOURCES = {
    'lavfi', 'testsrc', 'testsrc2', 'smptebars', 'smptehdbars', 'color', 'nullsrc',
    'rgbtestsrc', 'yuvtestsrc', 'sine', 'anoisesrc', 'aevalsrc', 'anullsrc',
    'allrgb', 'allyuv', 'pal75bars', 'pal100bars', 'gradients', 'life',
    'cellauto', 'mandelbrot', 'mptestsrc', 'haldclutsrc', 'flite',
}


def _is_frozen() -> bool:
    return bool(getattr(sys, 'frozen', False))


def ffmpeg_bin(name: str) -> str:
    """解析 ffmpeg/ffprobe 可执行文件路径：
    - 冻结模式：优先 exe 旁 backend/bin（首跑下载目录），否则包内 _MEIPASS/ffmpeg（旧版回退）
    - dev 模式：返回裸命令名（依赖系统 PATH）
    """
    if _is_frozen():
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        cands = [
            os.path.join(exe_dir, 'backend', 'bin', f'{name}.exe'),
            os.path.join(sys._MEIPASS, 'ffmpeg', f'{name}.exe'),
        ]
        for c in cands:
            if os.path.isfile(c):
                return c
        raise RuntimeError(
            f'未找到 {name}.exe（请先完成首次运行：应用会自动下载 ffmpeg；'
            f'若自动下载失败，可手动将 ffmpeg.exe/ffprobe.exe 放到 exe 旁 backend\\bin\\ 目录）'
        )
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


def _validate_input(value: str) -> bool:
    """校验 -i 输入源：
    - 虚拟源(lavfi/testsrc/color 等)放行
    - 网络协议(http/https/rtmp...)一律拒绝(防 SSRF)
    - 本地路径必须落在 UPLOAD/DOWNLOAD 目录内(防任意文件读取)
    """
    v = (value or '').strip()
    if not v:
        return False
    source = v.split('=')[0].split(':')[0]
    if source in VIRTUAL_SOURCES:
        return True
    if '://' in v:
        if v.startswith('file://'):
            v = v[len('file://'):]
        else:
            return False
    try:
        real = os.path.realpath(os.path.join(os.getcwd(), v))
    except Exception:
        return False
    for base in (os.path.realpath(UPLOAD), os.path.realpath(DOWNLOAD)):
        if real == base or real.startswith(base + os.sep):
            return True
    return False


def _check_inputs(parts: list) -> list:
    """返回所有非法输入源列表(空列表表示全部合法)。"""
    denied = []
    for i, p in enumerate(parts):
        if p == '-i' and i + 1 < len(parts):
            if not _validate_input(parts[i + 1]):
                denied.append(parts[i + 1])
    return denied


def _run_binary(run_parts: list, timeout: int, label: str, stop_event=None, proc_box=None):
    """执行 ffmpeg/ffprobe 并返回 (returncode, stdout_str, stderr_str)。
    - stdin 指向空设备,避免 ffmpeg 的 Overwrite 等交互提示阻塞
    - 输出写入临时文件(避免管道写满死锁),轮询等待,超时/收到停止信号时强制 kill
    """
    with tempfile.TemporaryFile() as fout, tempfile.TemporaryFile() as ferr:
        proc = subprocess.Popen(args=run_parts, stdin=subprocess.DEVNULL, stdout=fout, stderr=ferr)
        if proc_box is not None:
            proc_box[0] = proc
        try:
            deadline = time.monotonic() + timeout
            while proc.poll() is None:
                if stop_event is not None and stop_event.is_set():
                    proc.kill()
                    proc.wait()
                    raise InterruptedError(f'{label} 已被用户停止')
                if time.monotonic() > deadline:
                    proc.kill()
                    proc.wait()
                    ferr.seek(0)
                    err_tail = ferr.read().decode(errors='replace')[-2000:]
                    raise TimeoutError(f'{label} 执行超过 {timeout} 秒，已强制终止。{err_tail}')
                time.sleep(0.2)
            fout.seek(0)
            ferr.seek(0)
            return proc.returncode, fout.read().decode(errors='replace'), ferr.read().decode(errors='replace')
        finally:
            if proc_box is not None:
                proc_box[0] = None


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

    conf = ((config or {}).get('configurable') or {})
    stop_event = conf.get('stop_event')
    proc_box = conf.get('proc')

    with _execute_lock:
        # 清空下载目录，防止 ffmpeg 阻塞在 Overwrite? [y/N] 提示
        # 本次选中的输入文件（可能来自下载目录）需要保留，不能被清掉
        protected = {os.path.normpath(p) for p in (conf.get('selected_files') or [])}
        if os.path.exists(DOWNLOAD):
            for f in os.listdir(DOWNLOAD):
                fp = os.path.join(DOWNLOAD, f)
                if os.path.isfile(fp) and os.path.normpath(fp) not in protected:
                    os.remove(fp)

        try:
            run_parts = split_command(command)
            # 输入源安全校验：只允许 UPLOAD/DOWNLOAD 内的文件或 lavfi 虚拟源
            denied = _check_inputs(run_parts)
            if denied:
                return {
                    'command': command,
                    'command_result': '拒绝执行：输入源不在允许目录内或使用了被禁止的网络协议：'
                                     + '、'.join(denied) + '。请只使用 get_files 返回的文件。',
                }
            run_parts[0] = ffmpeg_bin('ffmpeg')
            returncode, _, stderr = _run_binary(run_parts, EXEC_TIMEOUT, 'ffmpeg', stop_event, proc_box)
            if returncode == 0:
                return {'command': command, 'flag': True, 'command_result': f'{command} 执行成功'}
            else:
                return {
                    'command': command,
                    'command_result': f'{command} 执行失败：{stderr[-2000:]}',
                }
        except OSError as e:
            return {
                'command': command,
                'command_result': f'命令执行异常：{e}',
            }
        except TimeoutError as e:
            return {
                'command': command,
                'command_result': f'命令执行超时：{e}',
            }
        except InterruptedError as e:
            return {
                'command': command,
                'command_result': f'{e}',
            }


@tool
def execute_probe_command(command: str, config: RunnableConfig):
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

    conf = ((config or {}).get('configurable') or {})
    stop_event = conf.get('stop_event')
    proc_box = conf.get('proc')

    try:
        run_parts = split_command(command)
        # 输入源安全校验(与 ffmpeg 相同)：防 SSRF 与任意文件读取
        denied = _check_inputs(run_parts)
        if denied:
            return {
                'command': command,
                'command_result': '拒绝执行：输入源不在允许目录内或使用了被禁止的网络协议：'
                                 + '、'.join(denied) + '。请只使用 get_files 返回的文件。',
            }
        run_parts[0] = ffmpeg_bin('ffprobe')
        returncode, stdout, stderr = _run_binary(run_parts, EXEC_TIMEOUT, 'ffprobe', stop_event, proc_box)
        if returncode == 0:
            output = stdout.strip()
            return {
                'command': command,
                'flag': True,
                'command_result': output or f'{command} 执行成功',
            }
        else:
            return {
                'command': command,
                'command_result': f'{command} 执行失败：{stderr[-2000:]}',
            }
    except OSError as e:
        return {
            'command': command,
            'command_result': f'命令执行异常：{e}',
        }
    except TimeoutError as e:
        return {
            'command': command,
            'command_result': f'命令执行超时：{e}',
        }
    except InterruptedError as e:
        return {
            'command': command,
            'command_result': f'{e}',
        }
